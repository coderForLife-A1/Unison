"""Notification ingestion adapters for Unison.

Provides a pluggable adapter system for pulling notifications from
different sources (IMAP email, JSON file drops, mock data for testing).

Quick start::

    from src.ingestion import get_adapter

    adapter = get_adapter("file")
    notifications = await adapter.fetch()

Available adapters:
    * ``mock``  — Generates realistic sample data (testing/demo only).
    * ``file``  — Watches a directory for ``.json`` notification files.
    * ``email`` — Polls an IMAP mailbox for unread messages.
"""

from __future__ import annotations

from src.ingestion.base import BaseAdapter
from src.ingestion.email_adapter import EmailAdapter
from src.ingestion.file_adapter import FileAdapter
from src.ingestion.mock_adapter import MockAdapter

__all__ = [
    "BaseAdapter",
    "EmailAdapter",
    "FileAdapter",
    "MockAdapter",
    "get_adapter",
]

# ── Adapter registry ────────────────────────────────────────────────────

_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "mock": MockAdapter,
    "file": FileAdapter,
    "email": EmailAdapter,
}


def get_adapter(name: str) -> BaseAdapter:
    """Factory: return an adapter instance by name.

    Args:
        name: One of ``"mock"``, ``"file"``, or ``"email"``
              (case-insensitive).

    Returns:
        An instantiated adapter ready to call ``fetch()``.

    Raises:
        ValueError: If *name* does not match a registered adapter.
    """
    key = name.strip().lower()
    adapter_cls = _ADAPTERS.get(key)
    if adapter_cls is None:
        available = ", ".join(sorted(_ADAPTERS))
        raise ValueError(
            f"Unknown adapter '{name}'. Available adapters: {available}"
        )
    return adapter_cls()
