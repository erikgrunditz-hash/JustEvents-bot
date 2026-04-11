"""Long-running JustEvent command listener.

Run this on a server to process `/create ...` command messages and create
Discord native scheduled events via the REST client.

Supports both:
- Auto-posted commands from sync jobs (listening to messages)
- Manual `/create` slash command for direct use
"""

import argparse
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from src.config import load_config
from src.discord_client import DiscordClient
from src.event_commands import parse_event_create_command
from src.models.event import Event

logger = logging.getLogger("justevents.listener")

_SOURCE_ID_RE = re.compile(r"source_id:\s*([^\s]+)", re.IGNORECASE)


def _parse_datetime(value: str) -> datetime:
    # Preferred format produced by this project: 2026-04-11 19:30 UTC
    value = value.strip()
    
    # Try full format first
    for fmt in ("%Y-%m-%d %H:%M UTC", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    
    # Try date-only format (default to 10:00 UTC)
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(hour=10, minute=0, tzinfo=timezone.utc)
    except ValueError:
        pass
    
    raise ValueError(f"Unsupported datetime format: {value!r}. Use format: YYYY-MM-DD or YYYY-MM-DD HH:MM")


def _parse_duration(value: str) -> timedelta:
    text = value.strip().lower()
    if not text:
        return timedelta(hours=2)

    total_minutes = 0
    chunks = re.findall(r"(\d+)([hm])", text)
    if chunks:
        for amount, unit in chunks:
            number = int(amount)
            total_minutes += number * 60 if unit == "h" else number
    elif text.endswith("m") and text[:-1].isdigit():
        total_minutes = int(text[:-1])
    elif text.endswith("h") and text[:-1].isdigit():
        total_minutes = int(text[:-1]) * 60
    else:
        raise ValueError(f"Unsupported duration format: {value!r}")

    return timedelta(minutes=max(1, total_minutes))


def _extract_source_id(description: str) -> str:
    match = _SOURCE_ID_RE.search(description or "")
    if match:
        return match.group(1).strip()
    return "justevent:manual"


def _extract_user_friendly_error(exc: Exception) -> str:
    """Extract user-friendly error message from exception."""
    exc_str = str(exc)
    
    # Handle Discord API validation errors
    if "Cannot schedule event in the past" in exc_str:
        return "❌ Cannot schedule event in the past. Please use a future date and time."
    if "GUILD_SCHEDULED_EVENT_SCHEDULE_PAST" in exc_str:
        return "❌ Event date/time is in the past. Please use a future date. Format: YYYY-MM-DD or YYYY-MM-DD HH:MM"
    if "Invalid Form Body" in exc_str:
        return "❌ Invalid event data. Please check all fields are correctly formatted."
    if "Rate limited" in exc_str or "429" in exc_str:
        return "❌ Rate limited by Discord. Please try again in a moment."
    
    # Generic fallback
    return f"❌ Failed to create event: {exc_str[:150]}"


def _ensure_creation_method_marker(description: str) -> str:
    """Ensure creation_method marker is in description for deduplication."""
    desc = description or ""
    if "creation_method:" in desc.lower():
        return desc
    suffix = "\n\ncreation_method: justevent"
    return desc + suffix


def _create_event_with_retry(
    rest_client: DiscordClient, event: Event, retries: int = 3, base_delay: float = 1.0
) -> dict:
    """Create an event via REST API with exponential backoff and rate limit handling."""
    for attempt in range(retries):
        try:
            return rest_client.create_event(event)
        except requests.HTTPError as exc:
            resp = exc.response
            if resp is not None and resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("retry_after", 5))
                except Exception:
                    retry_after = 5.0
                logger.warning(f"Rate limited by Discord — waiting {retry_after}s ...")
                time.sleep(retry_after)
                continue
            if attempt == retries - 1:
                raise
            wait = base_delay * (attempt + 1)
            logger.warning(f"HTTP error on attempt {attempt + 1}/{retries}: {exc} — retrying in {wait}s")
            time.sleep(wait)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = base_delay * (attempt + 1)
            logger.warning(f"Error on attempt {attempt + 1}/{retries}: {exc} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Failed to create event after {retries} retries")


def run_listener(config_path: str) -> None:
    try:
        import discord
        from discord.ext import commands
    except ImportError as exc:
        raise RuntimeError(
            "discord.py is required for JustEvent listener mode. "
            "Install runtime dependencies from requirements.txt."
        ) from exc

    config = load_config(config_path)
    discord_cfg = config.get("discord", {})
    bot_token = discord_cfg.get("bot_token", "")
    guild_id = str(discord_cfg.get("guild_id", "")).strip()

    listener_cfg = discord_cfg.get("justevent_listener", {})
    listen_channel_id = str(listener_cfg.get("listen_channel_id", "")).strip()

    if not bot_token or not guild_id:
        raise RuntimeError("DISCORD_BOT_TOKEN and DISCORD_GUILD_ID must be set.")
    if not listen_channel_id:
        raise RuntimeError(
            "discord.justevent_listener.listen_channel_id must be configured."
        )

    rest_client = DiscordClient(bot_token=bot_token, guild_id=guild_id)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True

    bot_client = commands.Bot(command_prefix="/", intents=intents)

    @bot_client.event
    async def on_ready():
        logger.info("JustEvent listener connected as %s", bot_client.user)
        logger.info("Listening for command messages in channel ID %s", listen_channel_id)
        # Sync slash commands
        try:
            synced = await bot_client.tree.sync()
            logger.info("Synced %d slash command(s)", len(synced))
        except Exception as exc:
            logger.warning("Failed to sync slash commands: %s", exc)

    async def _process_create_command(
        interaction_or_channel, title: str, datetime_str: str, description: str, duration_str: str, location: str
    ) -> None:
        """Shared logic for processing /create commands from both messages and slash commands."""
        try:
            start_time = _parse_datetime(datetime_str)
            duration = _parse_duration(duration_str)
            end_time = start_time + duration

            description = _ensure_creation_method_marker(description)
            source_id = _extract_source_id(description)

            # Avoid duplicate native events if a command is retried.
            # Wrap with retry for rate limiting
            try:
                existing = await asyncio.get_event_loop().run_in_executor(
                    None, rest_client.get_scheduled_events
                )
            except Exception as exc:
                logger.warning("Failed to fetch existing events: %s", exc)
                existing = []

            for discord_event in existing:
                existing_source_id = DiscordClient.extract_source_id(discord_event.get("description") or "")
                if existing_source_id == source_id:
                    msg = f"JustEvent: skipped duplicate source_id '{source_id}'"
                    if isinstance(interaction_or_channel, discord.TextChannel):
                        await interaction_or_channel.send(msg)
                    else:
                        # For interactions, use followup after deferred response
                        await interaction_or_channel.followup.send(msg, ephemeral=True)
                    return

            event = Event(
                source_id=source_id,
                source_url="https://discord.com/channels",
                title=title,
                description=description,
                start_time=start_time,
                end_time=end_time,
                source_name="JustEvent Listener",
                location=location,
                event_creation_method="justevent",
            )
            created = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _create_event_with_retry(rest_client, event)
            )
            msg = f"JustEvent: created native event '{created.get('name', event.title)}'"
            if isinstance(interaction_or_channel, discord.TextChannel):
                await interaction_or_channel.send(msg)
            else:
                # For interactions, use followup after deferred response
                await interaction_or_channel.followup.send(msg, ephemeral=True)
        except Exception as exc:
            logger.exception("Failed to process /create command")
            msg = _extract_user_friendly_error(exc)
            if isinstance(interaction_or_channel, discord.TextChannel):
                await interaction_or_channel.send(msg)
            else:
                # For interactions, use followup after deferred response
                await interaction_or_channel.followup.send(msg, ephemeral=True)

    @bot_client.event
    async def on_message(message):
        if str(message.channel.id) != listen_channel_id:
            return

        text = (message.content or "").strip()
        if not text.startswith("/create "):
            return

        try:
            args = parse_event_create_command(text)
            await _process_create_command(
                message.channel,
                title=args["title"],
                datetime_str=args["datetime"],
                description=args["description"],
                duration_str=args["duration"],
                location=args["location"],
            )
        except Exception as exc:
            logger.exception("Failed to parse /create message command")
            await message.channel.send(f"JustEvent: failed to parse command ({exc})")

    @bot_client.tree.command(
        name="create",
        description="Create a Discord scheduled event. Use format: title, date/time, description, duration, location"
    )
    @discord.app_commands.describe(
        title="Event title (required). Example: Team Meeting",
        datetime="Event date/time (required). Format: YYYY-MM-DD or YYYY-MM-DD HH:MM. Defaults to 10:00 UTC if time omitted. Example: 2026-04-15 or 2026-04-15 14:30",
        description="Event description (required). Example: Discuss Q2 roadmap",
        duration="Event duration (required). Format: 2h, 30m, or 1h30m. Example: 1h",
        location="Event location (required). Example: Discord Voice Channel"
    )
    async def create_event_command(
        interaction: discord.Interaction,
        title: str,
        datetime: str,
        description: str,
        duration: str,
        location: str,
    ):
        """Create a Discord scheduled event from slash command."""
        await interaction.response.defer(ephemeral=True)
        await _process_create_command(
            interaction,
            title=title,
            datetime_str=datetime,
            description=description,
            duration_str=duration,
            location=location,
        )

    bot_client.run(bot_token)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="JustEvents command listener for /create payloads."
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        metavar="PATH",
        help="Path to the YAML configuration file (default: config/config.yaml).",
    )
    args = parser.parse_args()
    run_listener(args.config)


if __name__ == "__main__":
    main()