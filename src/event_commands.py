"""Helpers for building and parsing command-style event creation messages."""

from datetime import datetime, timedelta, timezone
from typing import Dict

from src.models.event import Event


def format_event_create_command(event: Event, include_channel: bool = False) -> str:
    """Return a command payload compatible with Sesh-like workflows.

    The command is emitted as plain text so it can be copied/run manually,
    or consumed by the JustEvent listener bot.

    Args:
        event: The event to format.
        include_channel: If True, include channel:[#events] (for JustEvent mode).
                        If False, omit channel (for Sesh mode, since Sesh creates
                        events in the channel where the command was run).
    """
    start = _to_utc(event.start_time)
    duration = event.end_time - event.start_time
    if duration.total_seconds() <= 0:
        duration = timedelta(hours=2)

    title = _sanitize(event.title, max_len=64)
    description = _sanitize(event.description, max_len=750)
    location = _sanitize(event.location or "TBD", max_len=100)
    event_channel = _sanitize(event.command_target_channel or "#events", max_len=100)
    datetime_value = start.strftime("%Y-%m-%d %H:%M UTC")
    duration_value = _format_duration(duration)

    parts = [
        f"/create title: {title}",
        f"datetime: {datetime_value}",
        f"description: {description}",
        f"duration: {duration_value}",
        f"location: {location}",
    ]
    if include_channel:
        parts.append(f"channel: {event_channel}")

    return " ".join(parts)


def parse_event_create_command(command: str) -> Dict[str, str]:
    """Parse ``/create key: value key: value ...`` payloads emitted by this project."""
    text = (command or "").strip()
    if not text.startswith("/create "):
        raise ValueError("Command must start with '/create '")

    # Known keys in order they typically appear
    known_keys = {"title", "datetime", "description", "duration", "location", "channel"}
    
    # Find all key positions
    args: Dict[str, str] = {}
    remainder = text[len("/create "):].strip()
    
    while remainder:
        # Find the next key
        found_key = None
        found_pos = -1
        
        for key in known_keys:
            pattern = f"{key}: "
            pos = remainder.find(pattern)
            if pos != -1 and (found_pos == -1 or pos < found_pos):
                found_key = key
                found_pos = pos
        
        if found_key is None:
            break
        
        # Extract value from after "key: " until the next key
        value_start = found_pos + len(found_key) + 2  # +2 for ": "
        
        # Find next key
        next_key_pos = len(remainder)
        for key in known_keys:
            if key == found_key:
                continue
            pattern = f" {key}: "
            pos = remainder.find(pattern, value_start)
            if pos != -1 and pos < next_key_pos:
                next_key_pos = pos
        
        value = remainder[value_start:next_key_pos].strip()
        args[found_key] = value
        
        remainder = remainder[next_key_pos:].strip()
    
    required = {"title", "datetime", "description", "duration", "location"}
    missing = sorted(required - set(args))
    if missing:
        raise ValueError(f"Malformed command: missing required fields: {', '.join(missing)}")
    
    return args


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_duration(delta: timedelta) -> str:
    total_minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _sanitize(text: str, max_len: int) -> str:
    cleaned = (text or "").replace("\n", " ").replace("\r", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace("[", "(").replace("]", ")")
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "..."
    return cleaned
