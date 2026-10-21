"""LLM Unison Engine — local-first notification intelligence via Ollama.

Re-exports the three core engine classes for convenient access::

    from src.engine import OllamaClient, UnisonEngine, BatchProcessor
"""

from src.engine.llm_client import OllamaClient, OllamaOfflineError
from src.engine.unison import UnisonEngine
from src.engine.batch_processor import BatchProcessor

__all__ = [
    "OllamaClient",
    "OllamaOfflineError",
    "UnisonEngine",
    "BatchProcessor",
]
