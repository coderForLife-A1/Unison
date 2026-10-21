"""Batch processor with debouncing and sequential unison execution.

Prevents per-notification LLM inference overhead by collecting
notifications over a configured debounce window before triggering
deduplication and local inference.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from src.config import settings
from src.engine.unison import UnisonEngine
from src.models.schemas import IngestedNotification, UnisonSummary
from src.storage.deduplicator import Deduplicator

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Buffers notifications and processes them in batches to reduce local compute."""

    def __init__(
        self,
        deduplicator: Deduplicator,
        unison_engine: UnisonEngine,
        batch_window_seconds: Optional[int] = None,
    ) -> None:
        self.deduplicator = deduplicator
        self.unison_engine = unison_engine
        self.batch_window_seconds = (
            batch_window_seconds or settings.batch_window_seconds
        )
        self._buffer: list[IngestedNotification] = []
        self._last_flush: datetime = datetime.now()
        self._lock = asyncio.Lock()

    def add(self, notification: IngestedNotification) -> None:
        """Add a single notification to the processing buffer."""
        self._buffer.append(notification)

    def add_many(self, notifications: list[IngestedNotification]) -> None:
        """Add multiple notifications to the processing buffer."""
        self._buffer.extend(notifications)

    def should_flush(self) -> bool:
        """Check if buffer has accumulated items and the window has elapsed."""
        if not self._buffer:
            return False
        elapsed = (datetime.now() - self._last_flush).total_seconds()
        return elapsed >= self.batch_window_seconds

    async def flush(self) -> list[UnisonSummary]:
        """Deduplicate buffered notifications and run structured unison.

        Returns
        -------
        list[UnisonSummary]
            List of generated summaries for the batch.
        """
        async with self._lock:
            if not self._buffer:
                return []

            items_to_process = list(self._buffer)
            self._buffer.clear()
            self._last_flush = datetime.now()

            logger.info("Flushing %d notifications for batch unison...", len(items_to_process))

            # 1. Deduplicate
            batches = self.deduplicator.process(items_to_process)
            logger.info("Grouped into %d distinct batches.", len(batches))

            # 2. Unison sequentially
            summaries = await self.unison_engine.unison_batches(batches)
            return summaries

    @property
    def buffer_size(self) -> int:
        """Return the count of currently buffered items."""
        return len(self._buffer)

