"""Meetup.com event source.

Fetches upcoming events from a Meetup group using:
* **iCal feed** (primary, recommended) — includes accurate start/end times.
  URL pattern: ``https://www.meetup.com/{group_slug}/events/ical/``
* **RSS feed** (fallback) — does *not* include event start/end times; times
  will be unavailable and a warning is logged.
  URL pattern: ``https://www.meetup.com/{group_slug}/events/rss/``

iCal is preferred because Discord Scheduled Events require start and end times.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional

import requests
from icalendar import Calendar, vDatetime, vDDDLists

from src.models.event import Event
from src.sources.base import BaseSource

logger = logging.getLogger(__name__)

# Discord limits
_NAME_LIMIT = 100
_DESC_LIMIT = 1000

# Marker appended to every Discord event description so we can recognise
# events that were created by this bot and match them back to their source.
_SOURCE_FOOTER_TEMPLATE = (
    "\n\n🔗 *Mirrored from {source_name}*\n"
    "source_id: {source_id}\n"
    "creation_method: {creation_method}"
)


class MeetupSource(BaseSource):
    """Event source that reads from a Meetup.com group calendar."""

    _ICAL_URL = "https://www.meetup.com/{group_slug}/events/ical/"
    _RSS_URL = "https://www.meetup.com/{group_slug}/events/rss/"
    _DEFAULT_DURATION_HOURS = 2

    def __init__(self, config: dict) -> None:
        self._name: str = config["name"]
        self._group_slug: str = config["group_slug"]
        self._prefer_ical: bool = config.get("prefer_ical", True)
        # Path to a local sample file; used for offline / dry-run testing.
        self._local_sample: Optional[str] = config.get("local_sample")
        self._lookahead_days: int = config.get("lookahead_days", 90)
        self._include_past_events: bool = bool(
            config.get("include_past_events", bool(self._local_sample))
        )
        self._event_creation_method: str = str(config.get("event_creation_method", "direct")).lower()
        self._command_channel_id: Optional[str] = config.get("command_channel_id")
        self._command_target_channel: Optional[str] = config.get("command_target_channel", "#events")
        self._default_location: Optional[str] = config.get("default_location")
        self._command_ack_timeout_seconds: int = int(config.get("command_ack_timeout_seconds", 0) or 0)

    # ------------------------------------------------------------------
    # BaseSource interface
    # ------------------------------------------------------------------

    @property
    def source_type(self) -> str:
        return "meetup"

    @property
    def name(self) -> str:
        return self._name

    def fetch_events(self) -> List[Event]:
        if self._local_sample:
            logger.info(f"  Using local sample: {self._local_sample}")
            return self._load_local(self._local_sample)

        if self._prefer_ical:
            try:
                return self._fetch_ical()
            except Exception as exc:
                logger.warning(
                    f"  iCal fetch failed ({exc}); falling back to RSS "
                    f"(event times will be unavailable)."
                )
        return self._fetch_rss()

    # ------------------------------------------------------------------
    # iCal
    # ------------------------------------------------------------------

    def _fetch_ical(self) -> List[Event]:
        url = self._ICAL_URL.format(group_slug=self._group_slug)
        resp = requests.get(
            url,
            headers={"User-Agent": "JustEvents-Bot/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        return self._parse_ical(resp.content.decode("utf-8"))

    def _parse_ical(self, content: str) -> List[Event]:
        cal = Calendar.from_ical(content)
        now = datetime.now(tz=timezone.utc)
        cutoff = now + timedelta(days=self._lookahead_days)
        events: List[Event] = []

        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            # Skip cancelled events
            status = str(component.get("STATUS", "CONFIRMED")).upper()
            if status == "CANCELLED":
                continue
            try:
                event = self._ical_to_event(component, now, cutoff)
                if event is not None:
                    events.append(event)
            except Exception as exc:
                title = str(component.get("SUMMARY", "<unknown>"))
                logger.warning(f"  Skipping iCal event {title!r}: {exc}")

        return events

    def _ical_to_event(self, comp, now: datetime, cutoff: datetime) -> Optional[Event]:
        dtstart = comp.get("DTSTART")
        if dtstart is None:
            return None

        start_dt = self._to_aware_datetime(dtstart.dt)
        if (start_dt < now and not self._include_past_events) or start_dt > cutoff:
            return None

        dtend = comp.get("DTEND")
        if dtend is not None:
            end_dt = self._to_aware_datetime(dtend.dt)
        else:
            end_dt = start_dt + timedelta(hours=self._DEFAULT_DURATION_HOURS)

        # Ensure end is always after start (iCal data can be inconsistent)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=self._DEFAULT_DURATION_HOURS)

        url = str(comp.get("URL", "")).strip()
        uid = str(comp.get("UID", "")).strip()
        event_id = self._extract_meetup_id(url) or uid or url
        source_id = f"meetup:{event_id}"

        summary = str(comp.get("SUMMARY", "Untitled Event")).strip()
        raw_desc = str(comp.get("DESCRIPTION", "")).strip()
        description = self._build_description(raw_desc, source_id)

        location = str(comp.get("LOCATION", "")).strip() or self._default_location or None

        return Event(
            source_id=source_id,
            source_url=url or f"https://www.meetup.com/{self._group_slug}/events/",
            title=summary[:_NAME_LIMIT],
            description=description,
            start_time=start_dt,
            end_time=end_dt,
            location=location,
            source_name=self._name,
            event_creation_method=self._event_creation_method,
            command_channel_id=self._command_channel_id,
            command_target_channel=self._command_target_channel,
            command_ack_timeout_seconds=self._command_ack_timeout_seconds,
        )

    # ------------------------------------------------------------------
    # RSS (fallback — no event start/end times in Meetup RSS)
    # ------------------------------------------------------------------

    def _fetch_rss(self) -> List[Event]:
        url = self._RSS_URL.format(group_slug=self._group_slug)
        resp = requests.get(
            url,
            headers={"User-Agent": "JustEvents-Bot/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        return self._parse_rss(resp.content.decode("utf-8"))

    def _parse_rss(self, content: str) -> List[Event]:
        logger.warning(
            "Parsing Meetup RSS feed: event start/end times are NOT included in the "
            "Meetup RSS format. The iCal feed is strongly recommended instead."
        )
        root = ET.fromstring(content)
        items = root.findall(".//item")
        events: List[Event] = []
        for item in items:
            try:
                event = self._rss_item_to_event(item)
                if event is not None:
                    events.append(event)
            except Exception as exc:
                title = item.findtext("title", "<unknown>")
                logger.warning(f"  Skipping RSS item {title!r}: {exc}")
        return events

    def _rss_item_to_event(self, item: ET.Element) -> Optional[Event]:
        title = (item.findtext("title") or "Untitled Event").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        pub_date_str = (item.findtext("pubDate") or "").strip()
        raw_desc = (item.findtext("description") or "").strip()

        try:
            pub_dt = parsedate_to_datetime(pub_date_str).astimezone(timezone.utc)
        except Exception:
            pub_dt = datetime.now(tz=timezone.utc)

        event_id = self._extract_meetup_id(link) or guid
        source_id = f"meetup:{event_id}"

        note = (
            "\n\n⚠️ *Event time unavailable via RSS — check the link for the exact schedule.*"
        )
        description = self._build_description(raw_desc + note, source_id)

        # Use pubDate as a stand-in; callers should treat this as approximate.
        start_dt = pub_dt
        end_dt = pub_dt + timedelta(hours=self._DEFAULT_DURATION_HOURS)

        return Event(
            source_id=source_id,
            source_url=link or f"https://www.meetup.com/{self._group_slug}/events/",
            title=title[:_NAME_LIMIT],
            description=description,
            start_time=start_dt,
            end_time=end_dt,
            source_name=self._name,
            location=self._default_location,
            event_creation_method=self._event_creation_method,
            command_channel_id=self._command_channel_id,
            command_target_channel=self._command_target_channel,
            command_ack_timeout_seconds=self._command_ack_timeout_seconds,
        )

    # ------------------------------------------------------------------
    # Local sample loader (for testing)
    # ------------------------------------------------------------------

    def _load_local(self, path_str: str) -> List[Event]:
        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(f"Local sample not found: {path}")
        content = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in (".ics", ".ical"):
            return self._parse_ical(content)
        # Treat everything else as RSS/XML
        return self._parse_rss(content)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_description(self, raw: str, source_id: str) -> str:
        """Truncate description and append the source footer."""
        footer = _SOURCE_FOOTER_TEMPLATE.format(
            source_name=self._name,
            source_id=source_id,
            creation_method=self._event_creation_method,
        )
        available = _DESC_LIMIT - len(footer)
        body = _clean_text(raw)
        if len(body) > available:
            body = body[: available - 1] + "…"
        return body + footer

    @staticmethod
    def _extract_meetup_id(url: str) -> Optional[str]:
        """Pull the Meetup event ID out of a URL like .../events/313653098/"""
        match = re.search(r"/events/([^/#?]+)/?", url)
        return match.group(1) if match else None

    @staticmethod
    def _to_aware_datetime(dt_or_date) -> datetime:
        """Convert ``date`` or naïve ``datetime`` to an aware ``datetime`` in UTC."""
        if isinstance(dt_or_date, datetime):
            if dt_or_date.tzinfo is None:
                return dt_or_date.replace(tzinfo=timezone.utc)
            return dt_or_date.astimezone(timezone.utc)
        # Plain date (all-day event) — treat as midnight UTC
        return datetime(
            dt_or_date.year,
            dt_or_date.month,
            dt_or_date.day,
            tzinfo=timezone.utc,
        )


def _clean_text(text: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text
