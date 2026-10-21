"""File-based ingestion adapter.

Watches a configurable directory for ``.json`` files, parses them into
``IngestedNotification`` objects, and moves successfully processed files
into a ``processed/`` subdirectory so they are not re-ingested.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from src.config import settings
from src.ingestion.base import BaseAdapter
from src.models.schemas import IngestedNotification

logger = logging.getLogger(__name__)


class FileAdapter(BaseAdapter):
    """Reads JSON notification files from a watch directory.

    Expected file format — a JSON array of objects whose keys match the
    ``IngestedNotification`` schema::

        [
            {
                "source": "SMS",
                "sender": "+1-800-555-0199",
                "content": "Your OTP is 123456"
            },
            ...
        ]

    After a file is successfully parsed, it is moved to a ``processed/``
    subdirectory to prevent duplicate ingestion.
    """

    def __init__(self) -> None:
        self._watch_dir = Path(settings.file_watch_dir)

    @property
    def name(self) -> str:
        """Return the adapter display name."""
        return "FileAdapter"

    # ── Public API ──────────────────────────────────────────────────────

    async def fetch(self) -> list[IngestedNotification]:
        """Scan the watch directory and ingest all ``.json`` files.

        Returns:
            A flat list of validated ``IngestedNotification`` objects
            from every successfully parsed file.
        """
        self._ensure_directories()

        json_files = list(self._watch_dir.glob("*.json"))
        if not json_files:
            return []

        all_notifications: list[IngestedNotification] = []

        for filepath in json_files:
            parsed = await self._parse_file(filepath)
            if parsed is not None:
                all_notifications.extend(parsed)
                self._move_to_processed(filepath)

        return all_notifications

    # ── Internal helpers ────────────────────────────────────────────────

    def _ensure_directories(self) -> None:
        """Create watch and processed directories if they don't exist."""
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        (self._watch_dir / "processed").mkdir(parents=True, exist_ok=True)

    async def _parse_file(
        self, filepath: Path
    ) -> list[IngestedNotification] | None:
        """Parse a single JSON file into a list of notifications.

        Args:
            filepath: Path to the ``.json`` file.

        Returns:
            Validated notification list, or ``None`` if the file was
            malformed or contained invalid entries.
        """
        try:
            raw_text = filepath.read_text(encoding="utf-8")
            data = json.loads(raw_text)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Skipping malformed file %s: %s", filepath.name, exc
            )
            return None

        if not isinstance(data, list):
            logger.warning(
                "Skipping %s: expected a JSON array at the top level.",
                filepath.name,
            )
            return None

        notifications: list[IngestedNotification] = []
        for idx, entry in enumerate(data):
            try:
                notifications.append(IngestedNotification.model_validate(entry))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping entry %d in %s: %s", idx, filepath.name, exc
                )

        return notifications if notifications else None

    def _move_to_processed(self, filepath: Path) -> None:
        """Move a parsed file into the ``processed/`` subdirectory.

        Args:
            filepath: Path to the successfully-parsed file.
        """
        dest = self._watch_dir / "processed" / filepath.name
        try:
            shutil.move(str(filepath), str(dest))
            logger.info("Moved %s → processed/", filepath.name)
        except OSError as exc:
            logger.warning(
                "Could not move %s to processed/: %s", filepath.name, exc
            )
