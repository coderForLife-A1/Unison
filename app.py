"""Streamlit dashboard for Unison.

A quiet, local-first dashboard to view aggregated, deduplicated,
and unisoned notifications.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import streamlit as st

from src.config import settings
from src.engine.batch_processor import BatchProcessor
from src.engine.llm_client import OllamaClient
from src.engine.unison import UnisonEngine
from src.ingestion import MockAdapter, get_adapter
from src.models.schemas import IngestedNotification, UnisonSummary
from src.storage.deduplicator import Deduplicator
from src.storage.embedder import EmbeddingService
from src.storage.vector_store import VectorStore


# ── Page Configuration ────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Unison — Notification Intelligence",
    page_icon="🦥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling & Custom CSS ─────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #6c757d;
        font-size: 1.05rem;
        margin-bottom: 1.5rem;
    }
    .notif-card {
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 14px;
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .badge {
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        display: inline-block;
    }
    .badge-high {
        background-color: #fee2e2;
        color: #991b1b;
    }
    .badge-low {
        background-color: #fef3c7;
        color: #92400e;
    }
    .badge-noise {
        background-color: #f1f5f9;
        color: #475569;
    }
    .badge-action {
        background-color: #dcfce7;
        color: #166534;
        margin-left: 6px;
    }
    .reply-box {
        background-color: #f8fafc;
        border-left: 3px solid #3b82f6;
        padding: 8px 12px;
        margin-top: 10px;
        font-size: 0.9rem;
        color: #334155;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Cached Resources (Zero-overhead initialization) ──────────────────────────

@st.cache_resource
def get_ollama_client() -> OllamaClient:
    return OllamaClient()

@st.cache_resource
def get_storage_pipeline():
    embedder = EmbeddingService()
    store = VectorStore()
    dedup = Deduplicator(embedder=embedder, store=store)
    return embedder, store, dedup


# ── Session State Setup ──────────────────────────────────────────────────────

if "summaries" not in st.session_state:
    st.session_state.summaries = []

if "raw_notifications" not in st.session_state:
    st.session_state.raw_notifications = []

if "last_ingest_time" not in st.session_state:
    st.session_state.last_ingest_time = None


# ── Processing Pipeline ──────────────────────────────────────────────────────

async def run_pipeline(source_name: str = "mock") -> list[UnisonSummary]:
    """Ingest, deduplicate, and unison incoming alerts."""
    adapter = get_adapter(source_name)
    raw_notifs: list[IngestedNotification] = await adapter.fetch()

    if not raw_notifs:
        return []

    st.session_state.raw_notifications.extend(raw_notifs)
    st.session_state.last_ingest_time = datetime.now().strftime("%H:%M:%S")

    _, _, dedup = get_storage_pipeline()
    ollama_client = get_ollama_client()

    batches = dedup.process(raw_notifs)

    # Check Ollama status
    if ollama_client.is_available():
        engine = UnisonEngine(llm_client=ollama_client)
        new_summaries = await engine.unison_batches(batches)
    else:
        # Graceful fallback summary when Ollama is offline
        new_summaries = [
            UnisonSummary(
                source=f"{b.source} - {b.primary_sender}",
                priority="Low",
                category="Offline Notice",
                summary=f"Batch with {len(b.notifications)} messages grouped. Ollama offline — run 'ollama serve' for AI summaries.",
                action_required=False,
                suggested_reply=None,
                batch_id=b.batch_id,
            )
            for b in batches
        ]

    st.session_state.summaries = new_summaries + st.session_state.summaries
    return new_summaries


# ── Sidebar Controls ─────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🦥 Unison")
    st.caption("Slow & Steady Local Intelligence")

    ollama_client = get_ollama_client()
    ollama_online = ollama_client.is_available()

    if ollama_online:
        st.success(f"Ollama Online ({settings.ollama_model})")
    else:
        st.warning(
            "⚠️ Ollama is offline.\n\n"
            "Run in terminal:\n"
            "`ollama serve`\n"
            f"`ollama pull {settings.ollama_model}`"
        )

    st.divider()

    st.subheader("Ingestion Controls")
    source_choice = st.selectbox(
        "Source Adapter",
        options=["mock", "file", "email"],
        format_func=lambda x: {
            "mock": "Mock Data (Demo)",
            "file": "Local File Watcher",
            "email": "IMAP Mailbox",
        }.get(x, x),
    )

    if st.button("📥 Ingest & Process Batch", use_container_width=True, type="primary"):
        with st.spinner("Processing notifications..."):
            asyncio.run(run_pipeline(source_choice))
            st.rerun()

    st.divider()

    st.subheader("Views & Filters")
    sloth_mode = st.toggle(
        "🦥 Sloth Mode (Actions Only)",
        value=False,
        help="Hides low priority and noise, showing only actionable notifications.",
    )

    priority_filter = st.multiselect(
        "Priority",
        options=["High", "Low", "Noise"],
        default=["High", "Low", "Noise"] if not sloth_mode else ["High"],
    )

    source_filter = st.multiselect(
        "Channel",
        options=["WhatsApp", "Email", "SMS", "System"],
        default=["WhatsApp", "Email", "SMS", "System"],
    )

    if st.button("🗑️ Clear Dashboard", use_container_width=True):
        st.session_state.summaries = []
        st.session_state.raw_notifications = []
        st.rerun()


# ── Main Content Area ────────────────────────────────────────────────────────

st.markdown('<div class="main-header">Unified Notification Digest</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">All notifications stay 100% on-device. Grouped by semantic context and unisoned locally.</div>',
    unsafe_allow_html=True,
)

# Metric counters
col1, col2, col3, col4 = st.columns(4)
total_summaries = len(st.session_state.summaries)
high_count = sum(1 for s in st.session_state.summaries if s.priority == "High")
action_count = sum(1 for s in st.session_state.summaries if s.action_required)
noise_count = sum(1 for s in st.session_state.summaries if s.priority == "Noise")

col1.metric("Total Digests", total_summaries)
col2.metric("High Priority", high_count)
col3.metric("Actions Needed", action_count)
col4.metric("Noise Filtered", noise_count)

st.divider()

# Filter summaries
displayed_summaries = [
    s for s in st.session_state.summaries
    if (s.priority in priority_filter)
    and any(src.lower() in s.source.lower() for src in source_filter)
]

if sloth_mode:
    displayed_summaries = [s for s in displayed_summaries if s.action_required]

if not displayed_summaries:
    st.info("No notifications to display. Click **'Ingest & Process Batch'** in the sidebar to simulate or ingest alerts.")
else:
    for idx, summary in enumerate(displayed_summaries):
        badge_class = {
            "High": "badge-high",
            "Low": "badge-low",
            "Noise": "badge-noise",
        }.get(summary.priority, "badge-noise")

        with st.container():
            st.markdown(
                f"""
                <div class="notif-card">
                    <div>
                        <span class="badge {badge_class}">{summary.priority}</span>
                        <span class="badge badge-noise">{summary.category}</span>
                        {"<span class='badge badge-action'>⚡ Action Required</span>" if summary.action_required else ""}
                        <strong style="margin-left: 10px; font-size: 0.95rem;">{summary.source}</strong>
                    </div>
                    <div style="margin-top: 10px; font-size: 1.05rem; font-weight: 500;">
                        {summary.summary}
                    </div>
                    {f'<div class="reply-box"><strong>💬 Draft Reply:</strong> {summary.suggested_reply}</div>' if summary.suggested_reply else ''}
                </div>
                """,
                unsafe_allow_html=True,
            )

