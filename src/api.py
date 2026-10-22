"""FastAPI backend server for Unison.

Exposes REST APIs for the React frontend (Desktop web, Android PWA/WebView,
and mobile clients).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pathlib import Path

from src.config import settings
from src.engine.llm_client import OllamaClient
from src.engine.unison import UnisonEngine
from src.ingestion import get_adapter
from src.models.schemas import IngestedNotification, UnisonSummary
from src.storage.deduplicator import Deduplicator
from src.storage.embedder import EmbeddingService
from src.storage.vector_store import VectorStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("unison.api")

app = FastAPI(
    title="Unison API",
    description="Local-First Notification Intelligence REST API",
    version="1.0.0",
)

# Enable CORS for desktop browsers, local Vite dev server, and mobile webviews
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global Pipeline Singletons ────────────────────────────────────────────────

_embedder: Optional[EmbeddingService] = None
_store: Optional[VectorStore] = None
_deduplicator: Optional[Deduplicator] = None
_ollama_client: Optional[OllamaClient] = None

# In-memory storage for active session summaries & raw alerts
_summaries: list[UnisonSummary] = []
_raw_notifications: list[IngestedNotification] = []


def get_pipeline():
    global _embedder, _store, _deduplicator, _ollama_client
    if _embedder is None:
        _embedder = EmbeddingService()
    if _store is None:
        _store = VectorStore()
    if _deduplicator is None:
        _deduplicator = Deduplicator(embedder=_embedder, store=_store)
    if _ollama_client is None:
        _ollama_client = OllamaClient()
    return _deduplicator, _ollama_client, _store


# ── Request / Response Models ─────────────────────────────────────────────────

class IngestRequest(BaseModel):
    source: str = Field(default="mock", description="Adapter name: mock, file, email")


class DirectNotificationInput(BaseModel):
    source: Literal["SMS", "WhatsApp", "Email", "System"]
    sender: str
    content: str


class StatusResponse(BaseModel):
    ollama_online: bool
    ollama_model: str
    total_summaries: int
    high_priority_count: int
    action_required_count: int
    noise_count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """Return health status of local Ollama inference and dashboard metrics."""
    _, ollama_client, _ = get_pipeline()
    is_online = ollama_client.is_available()

    high_c = sum(1 for s in _summaries if s.priority == "High")
    action_c = sum(1 for s in _summaries if s.action_required)
    noise_c = sum(1 for s in _summaries if s.priority == "Noise")

    return StatusResponse(
        ollama_online=is_online,
        ollama_model=settings.ollama_model,
        total_summaries=len(_summaries),
        high_priority_count=high_c,
        action_required_count=action_c,
        noise_count=noise_c,
    )


@app.get("/api/summaries", response_model=list[UnisonSummary])
async def list_summaries():
    """Get all triaged/unisoned notification summaries."""
    return _summaries


@app.post("/api/ingest", response_model=list[UnisonSummary])
async def trigger_ingest(req: IngestRequest):
    """Trigger ingestion from the specified adapter (mock, file, email)."""
    try:
        adapter = get_adapter(req.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    raw_alerts = await adapter.fetch()
    if not raw_alerts:
        return []

    return await _process_and_store(raw_alerts)


@app.post("/api/notifications/direct", response_model=UnisonSummary)
async def push_direct_notification(payload: DirectNotificationInput):
    """Endpoint for Android devices / desktop hooks to push incoming notifications in real-time."""
    notif = IngestedNotification(
        source=payload.source,
        sender=payload.sender,
        content=payload.content,
        timestamp=datetime.now(),
    )
    results = await _process_and_store([notif])
    if results:
        return results[0]
    raise HTTPException(status_code=500, detail="Failed to process notification")


@app.delete("/api/summaries")
async def clear_summaries():
    """Clear in-memory digest summaries."""
    global _summaries, _raw_notifications
    _summaries.clear()
    _raw_notifications.clear()
    return {"status": "cleared"}


async def _process_and_store(alerts: list[IngestedNotification]) -> list[UnisonSummary]:
    global _summaries, _raw_notifications
    _raw_notifications.extend(alerts)

    dedup, ollama_client, _ = get_pipeline()
    batches = dedup.process(alerts)

    new_summaries: list[UnisonSummary] = []

    if ollama_client.is_available():
        engine = UnisonEngine(llm_client=ollama_client)
        new_summaries = await engine.unison_batches(batches)
    else:
        # Graceful fallback when local model server isn't running
        new_summaries = [
            UnisonSummary(
                source=f"{b.source} - {b.primary_sender}",
                priority="Low",
                category="Offline Notice",
                summary=f"Batch of {len(b.notifications)} messages grouped. (Ollama offline — run 'ollama serve' for AI intelligence).",
                action_required=False,
                suggested_reply=None,
                batch_id=b.batch_id,
            )
            for b in batches
        ]

    # Prepend newest summaries to the list
    _summaries = new_summaries + _summaries
    return new_summaries


# ── Static React Assets (Serve bundled React App for desktop & mobile) ────────

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        file_path = _FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIST / "index.html")
