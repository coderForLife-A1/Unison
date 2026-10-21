"""Base adapter interface for notification ingestion.

All ingestion adapters must subclass ``BaseAdapter`` and implement the
async ``fetch`` method, which returns a list of ``IngestedNotification``
objects for downstream dedup + unison processing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.schemas import IngestedNotification


class BaseAdapter(ABC):
    """Abstract base class for all Unison ingestion adapters.

    Subclasses represent a single notification source (e.g. IMAP email,
    JSON file drop, mock data) and must implement the ``fetch`` coroutine.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name, used in logging and UI."""

    @abstractmethod
    async def fetch(self) -> list[IngestedNotification]:
        """Pull new notifications from the source.

        Returns:
            A list of validated ``IngestedNotification`` objects.
        """
