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
from datetime import datetime, timezone
from difflib import SequenceMatcher
import logging
import sys
import time
from typing import Dict, List

import requests

from src.config import load_config
from src.discord_client import DiscordClient
from src.event_commands import format_event_create_command
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
_COMMAND_CREATION_METHODS = {"sesh", "justevent"}
_DEFAULT_SIMILARITY_TIME_WINDOW_MINUTES = 180
_DEFAULT_SIMILARITY_MIN_TITLE_RATIO = 0.55
_DEFAULT_SIMILARITY_MIN_SCORE = 0.72
_UNTRACKED_COMMAND_MATCH_TIME_WINDOW_MINUTES = 90
_UNTRACKED_COMMAND_MIN_TITLE_RATIO = 0.8


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


def _source_prefix(source_id: str) -> str:
    return (source_id or "").split(":", 1)[0].lower()


def _normalise_title(title: str) -> str:
    lowered = (title or "").lower()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalise_title(left), _normalise_title(right)).ratio()


def _parse_discord_start(discord_event: dict) -> datetime | None:
    raw = (discord_event.get("scheduled_start_time") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _find_similar_existing_event(
    incoming_event: Event,
    existing_discord: List[dict],
    used_discord_ids: set[str],
    *,
    time_window_minutes: int,
    min_title_ratio: float,
    min_score: float,
) -> dict | None:
    incoming_prefix = _source_prefix(incoming_event.source_id)
    if not incoming_prefix:
        return None

    best_candidate = None
    best_score = -1.0
    incoming_start = incoming_event.start_time.astimezone(timezone.utc)
    incoming_method = (incoming_event.event_creation_method or "direct").lower()

    for discord_event in existing_discord:
        discord_id = discord_event.get("id")
        if not discord_id or discord_id in used_discord_ids:
            continue

        existing_source_id = DiscordClient.extract_source_id(discord_event.get("description") or "")
        if existing_source_id:
            if _source_prefix(existing_source_id) != incoming_prefix:
                continue

            existing_method = (
                DiscordClient.extract_creation_method(discord_event.get("description") or "")
                or "direct"
            ).lower()
            if existing_method != incoming_method:
                continue
        else:
            # Sesh/manual-created events may have no metadata footer. For command-based
            # modes we still want to suppress duplicates by matching title/time only.
            if incoming_method not in _COMMAND_CREATION_METHODS:
                continue

        existing_start = _parse_discord_start(discord_event)
        if existing_start is None:
            continue

        diff_minutes = abs((incoming_start - existing_start).total_seconds()) / 60
        active_time_window = time_window_minutes
        if not existing_source_id:
            active_time_window = min(
                time_window_minutes,
                _UNTRACKED_COMMAND_MATCH_TIME_WINDOW_MINUTES,
            )

        if diff_minutes > active_time_window:
            continue

        title_ratio = _title_similarity(incoming_event.title, discord_event.get("name", ""))
        active_min_title_ratio = min_title_ratio
        if not existing_source_id:
            active_min_title_ratio = max(min_title_ratio, _UNTRACKED_COMMAND_MIN_TITLE_RATIO)

        if title_ratio < active_min_title_ratio:
            continue

        time_score = 1.0 - (diff_minutes / active_time_window)
        score = (0.7 * time_score) + (0.3 * title_ratio)
        if score >= min_score and score > best_score:
            best_score = score
            best_candidate = discord_event

    return best_candidate


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
    similarity_cfg = config.get("sync", {}).get("similarity_matching", {})
    similarity_time_window_minutes = int(
        similarity_cfg.get("time_window_minutes", _DEFAULT_SIMILARITY_TIME_WINDOW_MINUTES)
    )
    similarity_min_title_ratio = float(
        similarity_cfg.get("min_title_ratio", _DEFAULT_SIMILARITY_MIN_TITLE_RATIO)
    )
    similarity_min_score = float(
        similarity_cfg.get("min_score", _DEFAULT_SIMILARITY_MIN_SCORE)
    )
    excluded_title_terms = [
        str(term).strip().lower()
        for term in config.get("sync", {}).get("exclude_title_contains", [])
        if str(term).strip()
    ]

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
    # ---- Optional exclusions by title --------------------------------------------
    excluded_source_ids: set[str] = set()
    if excluded_title_terms:
        filtered_incoming: Dict[str, Event] = {}
        for sid, event in incoming_by_id.items():
            title_lc = event.title.lower()
            if any(term in title_lc for term in excluded_title_terms):
                excluded_source_ids.add(sid)
                continue
            filtered_incoming[sid] = event
        incoming_by_id = filtered_incoming

        logger.info(
            "Excluded %d incoming event(s) by title filter (sync.exclude_title_contains).",
            len(excluded_source_ids),
        )
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
    matched_existing_source_ids: set[str] = set()
    used_discord_ids: set[str] = set()

    created = updated = cancelled = skipped = failed = 0

    # ---- Create or update events --------------------------------------------------------
    for source_id, event in incoming_by_id.items():
        method = (event.event_creation_method or "direct").lower()
        if method not in {"direct", "sesh", "justevent"}:
            logger.warning(
                "Unknown event_creation_method=%r for %r; falling back to direct.",
                event.event_creation_method,
                source_id,
            )
            method = "direct"

        matched_discord_event = existing_by_source_id.get(source_id)
        matched_source_id = source_id if matched_discord_event else None

        if matched_discord_event is None:
            matched_discord_event = _find_similar_existing_event(
                event,
                existing_discord,
                used_discord_ids,
                time_window_minutes=similarity_time_window_minutes,
                min_title_ratio=similarity_min_title_ratio,
                min_score=similarity_min_score,
            )
            if matched_discord_event is not None:
                matched_source_id = DiscordClient.extract_source_id(
                    matched_discord_event.get("description") or ""
                )
                logger.info(
                    "Matched similar event for %r (incoming=%s, existing=%s)",
                    event.title,
                    source_id,
                    matched_source_id,
                )

        if method in _COMMAND_CREATION_METHODS:
            if matched_discord_event is not None:
                logger.info(
                    "Skipping existing event in %s mode: %r",
                    method,
                    event.title,
                )
                if matched_source_id:
                    matched_existing_source_ids.add(matched_source_id)
                existing_id = matched_discord_event.get("id")
                if existing_id:
                    used_discord_ids.add(existing_id)
                skipped += 1
                continue

            command_channel_id = (event.command_channel_id or "").strip()
            if not command_channel_id:
                logger.error(
                    "Cannot create %r in %s mode: missing command_channel_id in source config.",
                    event.title,
                    method,
                )
                failed += 1
                continue

            command = format_event_create_command(
                event,
                include_channel=(method == "justevent"),
                include_metadata=(method == "justevent"),
            )
            if dry_run:
                logger.info("[DRY RUN] Would post %s command: %s", method, command)
                created += 1
                continue

            logger.info("Posting %s command for %r", method, event.title)
            _with_retry(lambda cid=command_channel_id, cmd=command: client.send_channel_message(cid, cmd))
            time.sleep(_DISCORD_CALL_DELAY)
            created += 1

            if method == "justevent":
                ack_timeout = max(0, int(event.command_ack_timeout_seconds or 0))
                if ack_timeout > 0:
                    if _wait_for_event_creation(client, source_id, timeout_seconds=ack_timeout):
                        logger.info("JustEvent listener confirmed event creation for source_id=%s", source_id)
                    else:
                        logger.error(
                            "No JustEvent creation ack within %ss for source_id=%s. "
                            "The listener bot may be offline or missing permissions.",
                            ack_timeout,
                            source_id,
                        )
                        failed += 1
            continue

        if matched_discord_event is not None:
            de = matched_discord_event
            discord_id = de["id"]
            status = de.get("status", _STATUS_SCHEDULED)
            if matched_source_id:
                matched_existing_source_ids.add(matched_source_id)
            used_discord_ids.add(discord_id)

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
        if source_id in excluded_source_ids:
            skipped += 1
            continue

        if source_id not in incoming_by_id and source_id not in matched_existing_source_ids:
            existing_method = (
                DiscordClient.extract_creation_method(de.get("description") or "")
                or "direct"
            )
            if existing_method in _COMMAND_CREATION_METHODS:
                skipped += 1
                continue

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
        f"{created} created, {updated} updated, {cancelled} cancelled, {skipped} skipped, {failed} failed."
    )


# Discord GuildScheduledEvent status values
_STATUS_SCHEDULED = 1
_STATUS_ACTIVE = 2
_STATUS_COMPLETED = 3
_STATUS_CANCELLED = 4


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


def _wait_for_event_creation(client: DiscordClient, source_id: str, timeout_seconds: int) -> bool:
    """Poll Discord events for up to ``timeout_seconds`` looking for ``source_id``."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            events = client.get_scheduled_events()
        except Exception as exc:
            logger.warning("Ack poll failed while waiting for source_id=%s: %s", source_id, exc)
            time.sleep(2)
            continue

        for discord_event in events:
            existing_source_id = DiscordClient.extract_source_id(discord_event.get("description") or "")
            if existing_source_id == source_id:
                return True

        time.sleep(2)
    return False


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
