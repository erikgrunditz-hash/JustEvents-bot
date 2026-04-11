from datetime import datetime, timedelta, timezone

import pytest

from src.event_commands import format_event_create_command, parse_event_create_command
from src.models.event import Event


def _sample_event() -> Event:
    start = datetime(2026, 4, 11, 18, 30, tzinfo=timezone.utc)
    end = start + timedelta(hours=3, minutes=15)
    return Event(
        source_id="meetup:123",
        source_url="https://www.meetup.com/x/events/123/",
        title="Board Games Friday",
        description="source_id: meetup:123\ncreation_method: sesh",
        start_time=start,
        end_time=end,
        source_name="Meetup Test",
        location="Gothenburg",
        event_creation_method="sesh",
        command_target_channel="#events",
    )


def test_format_command_contains_required_fields():
    command = format_event_create_command(_sample_event(), include_channel=True)
    assert command.startswith("/create ")
    assert "title: Board Games Friday" in command
    assert "datetime: 2026-04-11 18:30 UTC" in command
    assert "duration: 3h15m" in command
    assert "location: Gothenburg" in command
    assert "channel: #events" in command


def test_format_command_omits_channel_for_sesh():
    command = format_event_create_command(_sample_event(), include_channel=False)
    assert "channel:" not in command
    assert "title: Board Games Friday" in command


def test_parse_command_round_trip():
    event = _sample_event()
    command = format_event_create_command(event, include_channel=True)
    parsed = parse_event_create_command(command)

    assert parsed["title"] == "Board Games Friday"
    assert parsed["datetime"] == "2026-04-11 18:30 UTC"
    assert parsed["duration"] == "3h15m"
    assert parsed["location"] == "Gothenburg"
    assert parsed["channel"] == "#events"
    assert "source_id: meetup:123" in parsed["description"]


@pytest.mark.parametrize(
    "bad_command",
    [
        "",
        "/create title:[A] datetime:[2026-01-01 10:00 UTC]",
        "/create title:A datetime:[2026-01-01 10:00 UTC] description:[x] duration:[1h] location:[x] channel:[#events]",
    ],
)
def test_parse_command_rejects_invalid_payloads(bad_command: str):
    with pytest.raises(ValueError):
        parse_event_create_command(bad_command)
