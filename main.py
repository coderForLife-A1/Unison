"""Unison — Local-first notification intelligence.

Entry point that bootstraps the application.
Supports two modes:
  • Dashboard  — launches the Streamlit UI  (default)
  • CLI        — runs a single ingest-and-unison cycle in the terminal
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def _check_ollama() -> bool:
    """Quick pre-flight check that Ollama is reachable."""
    try:
        from src.engine.llm_client import OllamaClient

        client = OllamaClient()
        return client.is_available()
    except Exception:
        return False


def run_dashboard() -> None:
    """Launch the Streamlit dashboard via subprocess."""
    import subprocess

    print("🦥 Starting Unison Dashboard …")

    if not _check_ollama():
        print(
            "\n⚠️  Ollama is not running!\n"
            "   Please start it with:  ollama serve\n"
            "   Then re-run Unison.\n"
            "   (The dashboard will still open, but unison will be disabled.)\n"
        )

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless=true"],
        check=False,
    )


async def run_cli_cycle() -> None:
    """Run a single ingest → dedup → unison cycle in the terminal."""
    from src.ingestion import MockAdapter
    from src.storage.embedder import EmbeddingService
    from src.storage.vector_store import VectorStore
    from src.storage.deduplicator import Deduplicator
    from src.engine.llm_client import OllamaClient
    from src.engine.unison import UnisonEngine

    print("🦥 Unison — CLI unison cycle\n")

    # 1. Ingest
    adapter = MockAdapter()
    notifications = await adapter.fetch()
    print(f"📥 Ingested {len(notifications)} notifications\n")

    # 2. Dedup
    embedder = EmbeddingService()
    store = VectorStore()
    dedup = Deduplicator(embedder, store)
    batches = dedup.process(notifications)
    print(f"🔗 Deduplicated into {len(batches)} batches\n")

    # 3. Unison
    llm = OllamaClient()
    if not llm.is_available():
        print("⚠️  Ollama is offline — skipping unison.")
        print("   Start it with: ollama serve")
        return

    engine = UnisonEngine(llm)
    summaries = await engine.unison_batches(batches)

    # 4. Display
    for s in summaries:
        icon = {"High": "🔴", "Low": "🟡", "Noise": "⚪"}.get(s.priority, "❓")
        print(f"{icon} [{s.priority}] {s.category} — {s.summary}")
        if s.action_required:
            print(f"   ➡️  Action required!")
        if s.suggested_reply:
            print(f"   💬 Suggested reply: {s.suggested_reply}")
        print()


def main() -> None:
    """Parse arguments and run the selected mode."""
    parser = argparse.ArgumentParser(
        prog="unison",
        description="🦥 Unison — Slow and Steady Notification Intelligence",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run a single CLI unison cycle instead of the dashboard",
    )
    args = parser.parse_args()

    if args.cli:
        asyncio.run(run_cli_cycle())
    else:
        run_dashboard()


if __name__ == "__main__":
    main()

