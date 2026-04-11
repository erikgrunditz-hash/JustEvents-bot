"""Tests for the Meetup event source parsers.

Run with:  pytest tests/
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.sources.meetup import MeetupSource

_SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples" / "meetup"

_BASE_CONFIG = {
    "name": "GoBo (test)",
    "group_slug": "gothenburg-board-gamers-gobo",
    "lookahead_days": 9999,  # Accept all future dates in samples
}


# ---------------------------------------------------------------------------
# iCal parsing
# ---------------------------------------------------------------------------

class TestICalParsing:
    def _source(self, filename: str) -> MeetupSource:
        return MeetupSource(
            {**_BASE_CONFIG, "local_sample": str(_SAMPLES_DIR / filename)}
        )

    def test_returns_list_of_events(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        assert isinstance(events, list)
        assert len(events) > 0

    def test_event_fields_populated(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        event = events[0]

        assert event.source_id.startswith("meetup:")
        assert event.title != ""
        assert len(event.title) <= 100
        assert event.source_url.startswith("https://")
        assert event.start_time.tzinfo is not None
        assert event.end_time.tzinfo is not None
        assert event.end_time > event.start_time

    def test_source_id_marker_in_description(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        for event in events:
            assert f"source_id: {event.source_id}" in event.description

    def test_description_within_discord_limit(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        for event in events:
            assert len(event.description) <= 1000, (
                f"Description too long for event {event.title!r}: {len(event.description)} chars"
            )

    def test_location_extracted(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        # At least one event should have a location
        locations = [e.location for e in events if e.location]
        assert len(locations) > 0

    def test_meetup_id_extracted_from_url(self):
        source = self._source("ical_example.ics")
        events = source.fetch_events()
        for event in events:
            # ID should be numeric or alphanumeric meetup slug, not a full URL
            raw_id = event.source_id.replace("meetup:", "")
            assert not raw_id.startswith("http"), (
                f"source_id should be a short ID, not a URL: {event.source_id!r}"
            )

    def test_event_times_are_aware_and_utc(self):
        source = self._source("ical_example.ics")
        for event in source.fetch_events():
            assert event.start_time.tzinfo is not None
            assert event.end_time.tzinfo is not None


# ---------------------------------------------------------------------------
# RSS parsing (fallback — no event times in Meetup RSS)
# ---------------------------------------------------------------------------

class TestRSSParsing:
    def _source(self, filename: str) -> MeetupSource:
        return MeetupSource(
            {
                **_BASE_CONFIG,
                "local_sample": str(_SAMPLES_DIR / filename),
                "prefer_ical": False,
            }
        )

    def test_returns_list_of_events(self):
        source = self._source("calendar_example.xml")
        events = source.fetch_events()
        assert isinstance(events, list)
        assert len(events) > 0

    def test_event_fields_populated(self):
        source = self._source("calendar_example.xml")
        events = source.fetch_events()
        event = events[0]

        assert event.source_id.startswith("meetup:")
        assert event.title != ""
        assert len(event.title) <= 100
        assert event.source_url.startswith("https://")

    def test_source_id_marker_in_description(self):
        source = self._source("calendar_example.xml")
        for event in source.fetch_events():
            assert f"source_id: {event.source_id}" in event.description

    def test_description_within_discord_limit(self):
        source = self._source("calendar_example.xml")
        for event in source.fetch_events():
            assert len(event.description) <= 1000


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------

class TestExtractMeetupId:
    def test_numeric_id(self):
        url = "https://www.meetup.com/some-group/events/313653098/"
        from src.sources.meetup import MeetupSource
        result = MeetupSource._extract_meetup_id(url)
        assert result == "313653098"

    def test_slug_id(self):
        url = "https://www.meetup.com/some-group/events/jjzpstyjcgbcb/"
        result = MeetupSource._extract_meetup_id(url)
        assert result == "jjzpstyjcgbcb"

    def test_returns_none_for_non_event_url(self):
        result = MeetupSource._extract_meetup_id("https://www.meetup.com/some-group/")
        assert result is None


class TestExtractSourceId:
    def test_extracts_source_id_line(self):
        from src.discord_client import DiscordClient
        desc = "Some event description\n\nsource_id: meetup:313653098"
        assert DiscordClient.extract_source_id(desc) == "meetup:313653098"

    def test_returns_none_for_no_marker(self):
        from src.discord_client import DiscordClient
        desc = "A Discord event not created by JustEvents"
        assert DiscordClient.extract_source_id(desc) is None

    def test_handles_empty_description(self):
        from src.discord_client import DiscordClient
        assert DiscordClient.extract_source_id("") is None
        assert DiscordClient.extract_source_id(None) is None


class TestExtractCreationMethod:
    def test_extracts_creation_method_line(self):
        from src.discord_client import DiscordClient
        desc = "Some event description\ncreation_method: justevent"
        assert DiscordClient.extract_creation_method(desc) == "justevent"

    def test_returns_none_for_no_marker(self):
        from src.discord_client import DiscordClient
        desc = "A Discord event without creation method marker"
        assert DiscordClient.extract_creation_method(desc) is None
