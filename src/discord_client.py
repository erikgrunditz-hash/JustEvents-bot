"""Discord REST API client for managing Scheduled Events (Guild Events).

Uses the Discord REST API directly with a bot token — no persistent WebSocket
connection required, which makes it suitable for scheduled jobs (e.g. cron,
GitHub Actions).

Discord permissions required:
  - MANAGE_EVENTS  (to create, modify, and cancel scheduled events)

API reference:
  https://discord.com/developers/docs/resources/guild-scheduled-event
"""

import logging
import time
from typing import Dict, List, Optional

import requests

from src.models.event import Event

logger = logging.getLogger(__name__)

_API_BASE = "https://discord.com/api/v10"

# Marker prefix that we embed in Discord event descriptions so we can match
# Discord events back to their source without a separate state file.
_SOURCE_ID_MARKER = "source_id: "
_CREATION_METHOD_MARKER = "creation_method: "

# Discord field limits
_NAME_LIMIT = 100
_LOCATION_LIMIT = 100

# Entity type 3 = EXTERNAL (event at a physical location outside Discord)
_ENTITY_TYPE_EXTERNAL = 3
# Privacy level 2 = GUILD_ONLY
_PRIVACY_GUILD_ONLY = 2

# Scheduled event status codes
_STATUS_SCHEDULED = 1
_STATUS_ACTIVE = 2
_STATUS_COMPLETED = 3
_STATUS_CANCELLED = 4


class DiscordClient:
    """Thin wrapper around the Discord Scheduled Events REST API."""

    def __init__(self, bot_token: str, guild_id: str) -> None:
        self._guild_id = guild_id
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "JustEvents-Bot/1.0",
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_scheduled_events(self) -> List[dict]:
        """Return all scheduled events for the guild."""
        resp = self._session.get(
            f"{_API_BASE}/guilds/{self._guild_id}/scheduled-events",
            params={"with_user_count": "false"},
        )
        _raise_for_status(resp)
        return resp.json()

    def create_event(self, event: Event) -> dict:
        """Create a new Discord Scheduled Event and return the API response."""
        payload = _build_payload(event)
        resp = self._session.post(
            f"{_API_BASE}/guilds/{self._guild_id}/scheduled-events",
            json=payload,
        )
        _raise_for_status(resp)
        return resp.json()

    def update_event(self, discord_event_id: str, event: Event) -> dict:
        """Update an existing Discord Scheduled Event."""
        payload = _build_payload(event)
        resp = self._session.patch(
            f"{_API_BASE}/guilds/{self._guild_id}/scheduled-events/{discord_event_id}",
            json=payload,
        )
        _raise_for_status(resp)
        return resp.json()

    def cancel_event(self, discord_event_id: str) -> None:
        """Cancel a Discord Scheduled Event (sets status to CANCELLED)."""
        resp = self._session.patch(
            f"{_API_BASE}/guilds/{self._guild_id}/scheduled-events/{discord_event_id}",
            json={"status": _STATUS_CANCELLED},
        )
        _raise_for_status(resp)

    def send_channel_message(self, channel_id: str, content: str) -> dict:
        """Send a plain text message to a Discord text channel."""
        resp = self._session.post(
            f"{_API_BASE}/channels/{channel_id}/messages",
            json={"content": content},
        )
        _raise_for_status(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_source_id(discord_description: str) -> Optional[str]:
        """Read the ``source_id:`` line embedded in a Discord event description.

        Returns ``None`` when the description was not created by this bot.
        """
        for line in (discord_description or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(_SOURCE_ID_MARKER):
                return stripped[len(_SOURCE_ID_MARKER):].strip()
        return None

    @staticmethod
    def extract_creation_method(discord_description: str) -> Optional[str]:
        """Read the ``creation_method:`` line from a Discord event description."""
        for line in (discord_description or "").splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(_CREATION_METHOD_MARKER):
                return stripped[len(_CREATION_METHOD_MARKER):].strip()
        return None


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _build_payload(event: Event) -> dict:
    location = (event.location or event.source_url)[:_LOCATION_LIMIT]
    return {
        "name": event.title[:_NAME_LIMIT],
        "description": event.description,
        "scheduled_start_time": event.start_time.isoformat(),
        "scheduled_end_time": event.end_time.isoformat(),
        "privacy_level": _PRIVACY_GUILD_ONLY,
        "entity_type": _ENTITY_TYPE_EXTERNAL,
        "entity_metadata": {"location": location},
    }


def _raise_for_status(resp: requests.Response) -> None:
    """Like ``raise_for_status`` but includes the response body in the error."""
    if resp.ok:
        return
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    raise requests.HTTPError(
        f"Discord API error {resp.status_code}: {body}",
        response=resp,
    )
