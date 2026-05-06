from datetime import datetime, timedelta, timezone

from src.main import _find_similar_existing_event
from src.models.event import Event


_MATCHING_DEFAULTS = {
    "time_window_minutes": 180,
    "min_title_ratio": 0.55,
    "min_score": 0.72,
}


def _sample_event(*, source_id: str, title: str, start_time: datetime, method: str = "sesh") -> Event:
    return Event(
        source_id=source_id,
        source_url="https://www.meetup.com/group/events/123/",
        title=title,
        description=f"source_id: {source_id}\ncreation_method: {method}",
        start_time=start_time,
        end_time=start_time + timedelta(hours=2),
        source_name="Meetup",
        event_creation_method=method,
    )


def _discord_event(
    *,
    event_id: str,
    title: str,
    start_time: datetime,
    source_id: str | None = "meetup:old-id",
    method: str | None = "sesh",
) -> dict:
    description_lines = []
    if source_id is not None:
        description_lines.append(f"source_id: {source_id}")
    if method is not None:
        description_lines.append(f"creation_method: {method}")

    return {
        "id": event_id,
        "name": title,
        "description": "\n".join(description_lines),
        "scheduled_start_time": start_time.isoformat(),
        "status": 1,
    }


def test_matches_similar_title_and_nearby_time():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Friday Board Games - Special Edition",
        start_time=start,
    )
    existing = [
        _discord_event(
            event_id="123",
            source_id="meetup:old-id",
            title="Friday Board Games",
            start_time=start + timedelta(minutes=10),
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is not None
    assert match["id"] == "123"


def test_does_not_match_if_time_is_too_far_apart():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Friday Board Games",
        start_time=start,
    )
    existing = [
        _discord_event(
            event_id="123",
            source_id="meetup:old-id",
            title="Friday Board Games",
            start_time=start + timedelta(hours=8),
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is None


def test_does_not_match_different_creation_method():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Friday Board Games",
        start_time=start,
        method="sesh",
    )
    existing = [
        _discord_event(
            event_id="123",
            source_id="meetup:old-id",
            title="Friday Board Games",
            start_time=start,
            method="direct",
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is None


def test_does_not_reuse_already_matched_discord_event():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Friday Board Games",
        start_time=start,
    )
    existing = [
        _discord_event(
            event_id="123",
            source_id="meetup:old-id",
            title="Friday Board Games",
            start_time=start,
        )
    ]

    match = _find_similar_existing_event(incoming, existing, {"123"}, **_MATCHING_DEFAULTS)
    assert match is None


def test_matches_untracked_command_event_by_title_and_time():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Board Game Night",
        start_time=start,
        method="sesh",
    )
    existing = [
        _discord_event(
            event_id="123",
            title="Board Game Night",
            start_time=start + timedelta(minutes=15),
            source_id=None,
            method=None,
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is not None
    assert match["id"] == "123"


def test_does_not_match_untracked_event_in_direct_mode():
    start = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Board Game Night",
        start_time=start,
        method="direct",
    )
    existing = [
        _discord_event(
            event_id="123",
            title="Board Game Night",
            start_time=start,
            source_id=None,
            method=None,
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is None


def test_does_not_match_untracked_same_time_but_different_title():
    start = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
    incoming = _sample_event(
        source_id="meetup:new-id",
        title="Play board games with Gothenburg Boardgamers - Open for everyone",
        start_time=start,
        method="sesh",
    )
    existing = [
        _discord_event(
            event_id="123",
            title="This is a different event",
            start_time=start,
            source_id=None,
            method=None,
        )
    ]

    match = _find_similar_existing_event(incoming, existing, set(), **_MATCHING_DEFAULTS)
    assert match is None
