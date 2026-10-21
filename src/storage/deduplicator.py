"""Deduplication engine for Unison notifications.

Orchestrates :class:`EmbeddingService` and :class:`VectorStore` to
group semantically similar notifications arriving within a configurable
time window into :class:`NotificationBatch` objects.  Non-duplicate
notifications become single-item batches.

All new notifications are persisted in the vector store after
processing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.config import settings
from src.models.schemas import IngestedNotification, NotificationBatch
from src.storage.embedder import EmbeddingService
from src.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


class Deduplicator:
    """Groups duplicate / near-duplicate notifications via vector similarity.

    Parameters
    ----------
    embedder : EmbeddingService
        Service used to compute text embeddings.
    store : VectorStore
        LanceDB vector store used for similarity search and persistence.
    similarity_threshold : float | None
        Override cosine-similarity threshold (0–1).  Defaults to
        ``settings.dedup_similarity_threshold``.
    time_window_minutes : int | None
        Override the time window (in minutes) for dedup.  Defaults to
        ``settings.dedup_time_window_minutes``.
    """

    def __init__(
        self,
        embedder: EmbeddingService,
        store: VectorStore,
        similarity_threshold: float | None = None,
        time_window_minutes: int | None = None,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._threshold = similarity_threshold or settings.dedup_similarity_threshold
        self._time_window = timedelta(
            minutes=time_window_minutes or settings.dedup_time_window_minutes,
        )

    # ── Private helpers ─────────────────────────────────────

    @staticmethod
    def _merge_texts(texts: list[str]) -> str:
        """Build representative content from a list of raw texts.

        For a single item the original text is returned as-is.
        For multiple items the texts are joined with a separator and
        prefixed with a count so the LLM sees the full context.

        Parameters
        ----------
        texts : list[str]
            Raw content strings to merge.

        Returns
        -------
        str
            A single representative text for the batch.
        """
        if len(texts) == 1:
            return texts[0]
        header = f"[{len(texts)} similar messages]"
        body = "\n---\n".join(texts)
        return f"{header}\n{body}"

    def _is_within_time_window(
        self,
        match_timestamp_iso: str,
        reference: datetime,
    ) -> bool:
        """Check whether a stored entry falls inside the dedup window.

        Parameters
        ----------
        match_timestamp_iso : str
            ISO-formatted timestamp of the stored notification.
        reference : datetime
            Timestamp of the incoming notification to compare against.

        Returns
        -------
        bool
            *True* if the stored entry is within the time window.
        """
        try:
            match_dt = datetime.fromisoformat(match_timestamp_iso)
        except (ValueError, TypeError):
            return False
        return abs(reference - match_dt) <= self._time_window

    # ── Public API ──────────────────────────────────────────

    def process(
        self,
        notifications: list[IngestedNotification],
    ) -> list[NotificationBatch]:
        """Deduplicate a list of incoming notifications.

        Steps:
        1. Embed every notification's content in a single batch call.
        2. For each notification, search LanceDB for similar entries
           within the configured time window.
        3. Group duplicates together into :class:`NotificationBatch`
           objects; non-duplicates become single-item batches.
        4. Store **all** new notifications in the vector store.
        5. Set ``representative_content`` on each batch to a merged
           version of the grouped texts.

        Parameters
        ----------
        notifications : list[IngestedNotification]
            Raw notifications to deduplicate.

        Returns
        -------
        list[NotificationBatch]
            Deduplicated batches ready for the LLM unison engine.
        """
        if not notifications:
            return []

        # 1. Batch-embed all notification contents.
        texts = [n.content for n in notifications]
        embeddings = self._embedder.embed_batch(texts)

        # Track which incoming notification IDs have already been
        # assigned to a batch so we don't double-count them.
        assigned: set[str] = set()

        # Map from notification index → list of peer indices that are
        # duplicates of it (within *this* incoming batch).
        groups: dict[int, list[int]] = {}

        for i, (notif, emb) in enumerate(zip(notifications, embeddings)):
            if notif.id in assigned:
                continue

            # Start a new group with this notification as anchor.
            group_indices = [i]
            assigned.add(notif.id)

            # 2a. Check against other *incoming* notifications that
            #     haven't been assigned yet.
            for j in range(i + 1, len(notifications)):
                if notifications[j].id in assigned:
                    continue
                # Quick cosine similarity via dot-product (vectors from
                # bge-small are L2-normalised).
                sim = sum(a * b for a, b in zip(emb, embeddings[j]))
                time_ok = abs(notif.timestamp - notifications[j].timestamp) <= self._time_window
                if sim >= self._threshold and time_ok:
                    group_indices.append(j)
                    assigned.add(notifications[j].id)

            # 2b. Check against the *existing* vector store.
            existing_matches = self._store.search_similar(
                embedding=emb,
                top_k=10,
                threshold=self._threshold,
            )
            # Filter by time window.
            existing_matches = [
                m
                for m in existing_matches
                if self._is_within_time_window(
                    m.get("timestamp", ""),
                    notif.timestamp,
                )
            ]

            if existing_matches:
                logger.debug(
                    "Notification '%s' has %d existing duplicates in the store.",
                    notif.id,
                    len(existing_matches),
                )

            groups[i] = group_indices

        # 3. Build NotificationBatch objects from the groups.
        batches: list[NotificationBatch] = []
        for anchor_idx, member_indices in groups.items():
            batch_notifications = [notifications[idx] for idx in member_indices]
            batch_texts = [n.content for n in batch_notifications]
            representative = self._merge_texts(batch_texts)

            batch = NotificationBatch(
                notifications=batch_notifications,
                representative_content=representative,
            )
            batches.append(batch)
            logger.debug(
                "Batch '%s' contains %d notification(s).",
                batch.batch_id,
                len(batch_notifications),
            )

        # 4. Store all new notifications in the vector store.
        for notif, emb in zip(notifications, embeddings):
            self._store.add_notification(notif, emb)
        logger.info(
            "Processed %d notifications → %d batches.", len(notifications), len(batches),
        )

        return batches
