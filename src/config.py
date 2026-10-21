"""Centralised configuration for Unison.

Loads settings from the .env file (or environment variables) and exposes
them as a frozen settings object using pydantic v2.
"""

from __future__ import annotations

import os
from pathlib import Path
from pydantic import BaseModel, Field

# Project root is the directory containing this file's parent (src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Lightweight .env parser if python-dotenv isn't loaded
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

_load_env_file(_PROJECT_ROOT / ".env")


class UnisonSettings(BaseModel):
    """All Unison configuration values, typed and validated."""

    # ── Ollama ──────────────────────────────────────────────
    ollama_host: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        description="Ollama server URL",
    )
    ollama_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        description="Ollama model tag for unison inference",
    )

    # ── Embeddings ──────────────────────────────────────────
    embed_model: str = Field(
        default_factory=lambda: os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        description="FastEmbed model name for notification embeddings",
    )

    # ── LanceDB ─────────────────────────────────────────────
    lance_db_path: str = Field(
        default_factory=lambda: os.getenv("LANCE_DB_PATH", ".lancedb"),
        description="Directory for LanceDB file-based vector storage",
    )

    # ── Deduplication ───────────────────────────────────────
    dedup_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("DEDUP_SIMILARITY_THRESHOLD", "0.85")),
        description="Cosine similarity threshold for dedup (0-1)",
    )
    dedup_time_window_minutes: int = Field(
        default_factory=lambda: int(os.getenv("DEDUP_TIME_WINDOW_MINUTES", "30")),
        description="Only dedup against notifications within this time window",
    )

    # ── Batch Processing ────────────────────────────────────
    batch_window_seconds: int = Field(
        default_factory=lambda: int(os.getenv("BATCH_WINDOW_SECONDS", "30")),
        description="Seconds to wait collecting notifications before LLM unison",
    )

    # ── IMAP Email Ingestion ────────────────────────────────
    imap_host: str = Field(default_factory=lambda: os.getenv("IMAP_HOST", ""), description="IMAP server hostname")
    imap_port: int = Field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")), description="IMAP server port")
    imap_user: str = Field(default_factory=lambda: os.getenv("IMAP_USER", ""), description="IMAP username/email")
    imap_password: str = Field(default_factory=lambda: os.getenv("IMAP_PASSWORD", ""), description="IMAP password")
    imap_use_ssl: bool = Field(default_factory=lambda: os.getenv("IMAP_USE_SSL", "true").lower() in ("true", "1", "yes"), description="Use SSL for IMAP")
    imap_folder: str = Field(default_factory=lambda: os.getenv("IMAP_FOLDER", "INBOX"), description="IMAP folder to poll")

    # ── File Watch Ingestion ────────────────────────────────
    file_watch_dir: str = Field(
        default_factory=lambda: os.getenv("FILE_WATCH_DIR", "./data/inbox"),
        description="Directory to watch for JSON/CSV notification exports",
    )

    @property
    def lance_db_full_path(self) -> Path:
        """Resolve the LanceDB path relative to project root."""
        p = Path(self.lance_db_path)
        if not p.is_absolute():
            return _PROJECT_ROOT / p
        return p


# ── Singleton ───────────────────────────────────────────────
settings = UnisonSettings()


