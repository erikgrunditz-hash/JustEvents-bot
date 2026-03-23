"""Abstract base class for event sources."""

from abc import ABC, abstractmethod
from typing import List

from src.models.event import Event


class BaseSource(ABC):
    """All event source implementations must inherit from this class.

    To add a new source (e.g. Eventbrite, Facebook Events):
    1. Create ``src/sources/<name>.py``
    2. Subclass ``BaseSource``
    3. Implement ``source_type`` and ``fetch_events``
    4. Register the class in the ``SOURCE_REGISTRY`` dict in ``src/main.py``
    """

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Short identifier for this source type, e.g. ``'meetup'``."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this subscription, e.g. ``'GoBo on Meetup'``."""
        ...

    @abstractmethod
    def fetch_events(self) -> List[Event]:
        """Fetch and return a list of upcoming events from this source.

        Implementations should:
        - Only return events whose ``start_time`` is in the future.
        - Handle their own exceptions and raise them so the caller can log them.
        - Not mutate any shared state.
        """
        ...
