"""JustEvents — main entry point.

Usage:
    python -m src.main                      # normal sync
    python -m src.main --dry-run            # preview only, no Discord changes
    python -m src.main --config path/to/config.yaml

Adding a new source type:
    1. Create src/sources/<name>.py and subclass BaseSource.
    2. Add the class to SOURCE_REGISTRY below.
    3. Add an entry under ``sources:`` in config/config.yaml.
"""

import argparse
import logging
import sys
import time
from typing import Dict, List

import requests

from src.config import load_config
from src.discord_client import DiscordClient
from src.models.event import Event
from src.sources.base import BaseSource
from src.sources.meetup import MeetupSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("justevents")

# Register source implementations here.
SOURCE_REGISTRY: Dict[str, type] = {
    "meetup": MeetupSource,
}

# How long to wait between Discord API calls to avoid rate limits (seconds).
_DISCORD_CALL_DELAY = 0.5


def _build_sources(sources_config: list) -> List[BaseSource]:
    sources: List[BaseSource] = []
    for cfg in sources_config:
        source_type = cfg.get("type", "")
        cls = SOURCE_REGISTRY.get(source_type)
        if cls is None:
            logger.warning(f"Unknown source type {source_type!r} in config — skipping.")
            continue
        sources.append(cls(cfg))
    return sources


def sync(config: dict, dry_run: bool = False) -> None:
    """Fetch events from all configured sources and sync them to Discord."""
    discord_cfg = config.get("discord", {})
    bot_token: str = discord_cfg.get("bot_token", "")
    guild_id: str = discord_cfg.get("guild_id", "")

    if not bot_token or not guild_id:
        logger.error(
            "DISCORD_BOT_TOKEN and DISCORD_GUILD_ID must be set.\n"
            "  Set them in the .env file or as environment variables."
        )
        sys.exit(1)

    client = DiscordClient(bot_token=bot_token, guild_id=guild_id)
    sources = _build_sources(config.get("sources", []))

    if not sources:
        logger.warning("No sources configured. Add entries under 'sources:' in config.yaml.")
        return

    # ---- Gather incoming events from all sources ----------------------------------------
    incoming: List[Event] = []
    for source in sources:
        logger.info(f"Fetching events from {source.name!r} ...")
        try:
            events = source.fetch_events()
            logger.info(f"  {len(events)} upcoming event(s) found.")
            incoming.extend(events)
        except Exception as exc:
            logger.error(f"  Failed to fetch from {source.name!r}: {exc}")

    incoming_by_id: Dict[str, Event] = {e.source_id: e for e in incoming}

    # ---- Fetch existing Discord scheduled events ----------------------------------------
    logger.info("Fetching existing Discord scheduled events ...")
    if dry_run:
        existing_discord = []
    else:
        try:
            existing_discord = client.get_scheduled_events()
        except Exception as exc:
            logger.error(f"Failed to fetch Discord events: {exc}")
            sys.exit(1)

    # Build a source_id → discord_event map for events we previously created
    existing_by_source_id: Dict[str, dict] = {}
    for de in existing_discord:
        source_id = DiscordClient.extract_source_id(de.get("description") or "")
        if source_id:
            existing_by_source_id[source_id] = de

    created = updated = cancelled = skipped = 0

    # ---- Create or update events --------------------------------------------------------
    for source_id, event in incoming_by_id.items():
        if source_id in existing_by_source_id:
            de = existing_by_source_id[source_id]
            discord_id = de["id"]
            status = de.get("status", _STATUS_SCHEDULED)

            if status in (_STATUS_COMPLETED, _STATUS_CANCELLED):
                # Event was completed/cancelled in Discord but still exists in the source
                # — recreate it.
                action = f"Recreating (was completed/cancelled): {event.title!r}"
                if dry_run:
                    logger.info(f"[DRY RUN] Would {action}")
                else:
                    logger.info(action)
                    _with_retry(lambda e=event: client.create_event(e))
                    time.sleep(_DISCORD_CALL_DELAY)
                created += 1
            else:
                action = f"Updating: {event.title!r}"
                if dry_run:
                    logger.info(f"[DRY RUN] Would update: {event.title!r}")
                else:
                    logger.info(action)
                    _with_retry(lambda did=discord_id, e=event: client.update_event(did, e))
                    time.sleep(_DISCORD_CALL_DELAY)
                updated += 1
        else:
            if dry_run:
                logger.info(f"[DRY RUN] Would create: {event.title!r}  ({event.start_time.isoformat()})")
            else:
                logger.info(f"Creating: {event.title!r}  ({event.start_time.isoformat()})")
                _with_retry(lambda e=event: client.create_event(e))
                time.sleep(_DISCORD_CALL_DELAY)
            created += 1

    # ---- Cancel events that no longer appear in any source --------------------------------
    for source_id, de in existing_by_source_id.items():
        if source_id not in incoming_by_id:
            status = de.get("status", _STATUS_SCHEDULED)
            if status == _STATUS_SCHEDULED:
                name = de.get("name", "?")
                if dry_run:
                    logger.info(f"[DRY RUN] Would cancel: {name!r}  (no longer in source feed)")
                else:
                    logger.info(f"Cancelling: {name!r}  (no longer in source feed)")
                    _with_retry(lambda did=de["id"]: client.cancel_event(did))
                    time.sleep(_DISCORD_CALL_DELAY)
                cancelled += 1
            else:
                skipped += 1

    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        f"{label}Sync complete — "
        f"{created} created, {updated} updated, {cancelled} cancelled, {skipped} skipped."
    )


_STATUS_SCHEDULED = 1  # local alias used above


def _with_retry(fn, retries: int = 3, base_delay: float = 1.0):
    """Call ``fn()`` up to ``retries`` times, honouring Discord rate-limit responses."""
    for attempt in range(retries):
        try:
            return fn()
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JustEvents: mirror events from external sources to Discord."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch events and show what would change, but do not post to Discord.",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        metavar="PATH",
        help="Path to the YAML configuration file (default: config/config.yaml).",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    sync(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
