"""Common event model shared across all sources."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Event:
    """A normalised event that can originate from any source.

    All times must be timezone-aware.  Use UTC internally and convert when
    displaying to human-readable text.
    """

    # A globally unique identifier in "source_type:external_id" format.
    # Example: "meetup:313653098"
    source_id: str

    # URL pointing to the original event page.
    source_url: str

    title: str
    description: str

    # Both timestamps must be timezone-aware datetimes.
    start_time: datetime
    end_time: datetime

    # Human-readable label for the origin, e.g. "Meetup: GoBo"
    source_name: str = ""

    # Physical location string, or None for online/unknown events.
    location: Optional[str] = None

    # Optional banner/cover image URL.
    image_url: Optional[str] = None

    # Optional RSVP count scraped from the source.
    rsvp_count: Optional[int] = None

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None:
            raise ValueError(f"start_time must be timezone-aware (event: {self.title!r})")
        if self.end_time.tzinfo is None:
            raise ValueError(f"end_time must be timezone-aware (event: {self.title!r})")
        if self.end_time <= self.start_time:
            raise ValueError(f"end_time must be after start_time (event: {self.title!r})")
