"""LanceDB-backed vector store for notifications.

Manages a single LanceDB table (``notifications``) that persists
notification text alongside its embedding vector.  Supports cosine
similarity search for deduplication and retrieval of recent entries
for the Streamlit dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import lancedb
import pyarrow as pa

from src.config import settings
from src.models.schemas import IngestedNotification

logger = logging.getLogger(__name__)

# Name of the LanceDB table that stores notifications.
_TABLE_NAME = "notifications"


class VectorStore:
    """Manages notification vectors in a LanceDB embedded database.

    On instantiation the database directory is opened (or created).
    The actual table is created lazily on the first insertion so that
    we avoid schema issues when the DB is brand-new.

    Parameters
    ----------
    db_path : str | None
        Override the database directory path.  When *None*, the value
        from ``settings.lance_db_full_path`` is used.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        resolved_path = str(db_path or settings.lance_db_full_path)
        logger.info("Opening LanceDB at '%s'", resolved_path)
        self._db = lancedb.connect(resolved_path)
        self._table: Optional[lancedb.table.Table] = None
        self._try_open_table()

    # ── Private helpers ─────────────────────────────────────

    def _try_open_table(self) -> None:
        """Attempt to open an existing table; silently skip if absent."""
        try:
            if _TABLE_NAME in self._db.table_names():
                self._table = self._db.open_table(_TABLE_NAME)
                logger.info("Opened existing table '%s'.", _TABLE_NAME)
        except Exception:
            logger.debug("Table '%s' not found; will create on first insert.", _TABLE_NAME)

    def _ensure_table(self, vector_dim: int) -> lancedb.table.Table:
        """Create the table if it does not already exist.

        Parameters
        ----------
        vector_dim : int
            Dimensionality of the embedding vectors (used only when
            creating the table for the first time).

        Returns
        -------
        lancedb.table.Table
            The open LanceDB table.
        """
        if self._table is not None:
            return self._table

        schema = pa.schema(
            [
                pa.field("id", pa.utf8()),
                pa.field("source", pa.utf8()),
                pa.field("sender", pa.utf8()),
                pa.field("content", pa.utf8()),
                pa.field("timestamp", pa.utf8()),
                pa.field("vector", pa.list_(pa.float32(), vector_dim)),
            ]
        )

        logger.info("Creating table '%s' with vector dim %d.", _TABLE_NAME, vector_dim)
        self._table = self._db.create_table(_TABLE_NAME, schema=schema)
        return self._table

    # ── Public API ──────────────────────────────────────────

    def add_notification(
        self,
        notification: IngestedNotification,
        embedding: list[float],
    ) -> None:
        """Insert a single notification with its embedding vector.

        Parameters
        ----------
        notification : IngestedNotification
            The notification to store.
        embedding : list[float]
            Pre-computed embedding vector for the notification content.
        """
        table = self._ensure_table(vector_dim=len(embedding))

        record = {
            "id": notification.id,
            "source": notification.source,
            "sender": notification.sender,
            "content": notification.content,
            "timestamp": notification.timestamp.isoformat(),
            "vector": embedding,
        }
        table.add([record])
        logger.debug("Stored notification '%s'.", notification.id)

    def search_similar(
        self,
        embedding: list[float],
        top_k: int = 5,
        threshold: float = 0.85,
    ) -> list[dict]:
        """Find notifications with cosine similarity ≥ *threshold*.

        Parameters
        ----------
        embedding : list[float]
            Query vector to search against.
        top_k : int
            Maximum number of results to return.
        threshold : float
            Minimum cosine-similarity score (0–1).  Results below this
            threshold are filtered out.

        Returns
        -------
        list[dict]
            Matching rows as dicts, each augmented with a ``_distance``
            key (lower distance → higher similarity for cosine metric).
        """
        if self._table is None:
            return []

        try:
            results = (
                self._table.search(embedding)
                .metric("cosine")
                .limit(top_k)
                .to_pandas()
            )
        except Exception:
            logger.warning("Vector search failed; returning empty results.", exc_info=True)
            return []

        if results.empty:
            return []

        # LanceDB cosine metric returns *distance* (0 = identical).
        # Convert to similarity: sim = 1 - distance.
        results["_similarity"] = 1.0 - results["_distance"]
        filtered = results[results["_similarity"] >= threshold]

        # Convert to list[dict], dropping the vector column for readability.
        records = filtered.drop(columns=["vector"], errors="ignore").to_dict(orient="records")
        return records

    def get_recent(self, n: int = 50) -> list[dict]:
        """Return the *n* most recent notification entries.

        Parameters
        ----------
        n : int
            Number of recent entries to retrieve.

        Returns
        -------
        list[dict]
            Rows ordered newest-first.
        """
        if self._table is None:
            return []

        try:
            df = self._table.to_pandas()
        except Exception:
            logger.warning("Failed to read table; returning empty.", exc_info=True)
            return []

        if df.empty:
            return []

        # Sort by ISO timestamp descending and take the top N.
        df = df.sort_values("timestamp", ascending=False).head(n)
        return df.drop(columns=["vector"], errors="ignore").to_dict(orient="records")
