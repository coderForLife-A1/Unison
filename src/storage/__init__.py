"""Storage layer for Unison.

Provides embedding generation, vector storage (LanceDB), and
deduplication services for the notification pipeline.

Public API:
    EmbeddingService  — FastEmbed wrapper for text → vector conversion.
    VectorStore       — LanceDB-backed notification vector store.
    Deduplicator      — Orchestrates embedding + similarity search for dedup.
"""

from src.storage.embedder import EmbeddingService
from src.storage.vector_store import VectorStore
from src.storage.deduplicator import Deduplicator

__all__ = ["EmbeddingService", "VectorStore", "Deduplicator"]
