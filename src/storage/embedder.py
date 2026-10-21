"""Embedding service backed by FastEmbed.

Wraps the ``fastembed`` library to convert notification text into dense
vectors using the model specified in :pydata:`src.config.settings`
(default ``BAAI/bge-small-en-v1.5``).

The underlying model is **lazy-initialised** on first use so that the
import itself is effectively free.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastembed import TextEmbedding

from src.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generates text embeddings via FastEmbed.

    The model is loaded lazily on the first call to :meth:`embed_text` or
    :meth:`embed_batch`, keeping start-up cost negligible until embeddings
    are actually needed.

    Parameters
    ----------
    model_name : str | None
        Override the model tag.  When *None* (the default), the value from
        ``settings.embed_model`` is used.
    """

    def __init__(self, model_name: Optional[str] = None) -> None:
        self._model_name: str = model_name or settings.embed_model
        self._model: Optional[TextEmbedding] = None

    # ── Private helpers ─────────────────────────────────────

    def _ensure_model(self) -> TextEmbedding:
        """Load the FastEmbed model on first use.

        Returns
        -------
        TextEmbedding
            The ready-to-use FastEmbed model instance.
        """
        if self._model is None:
            logger.info("Loading FastEmbed model '%s' …", self._model_name)
            self._model = TextEmbedding(model_name=self._model_name)
            logger.info("FastEmbed model loaded.")
        return self._model

    # ── Public API ──────────────────────────────────────────

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string.

        Parameters
        ----------
        text : str
            The raw text to embed.

        Returns
        -------
        list[float]
            Dense vector representation of *text*.
        """
        model = self._ensure_model()
        # fastembed.embed() returns a generator of numpy arrays.
        embeddings = list(model.embed([text]))
        return embeddings[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single batch call.

        Parameters
        ----------
        texts : list[str]
            A list of raw text strings to embed.

        Returns
        -------
        list[list[float]]
            A list of dense vectors, one per input text, in the same order.
        """
        if not texts:
            return []

        model = self._ensure_model()
        embeddings = list(model.embed(texts))
        return [e.tolist() for e in embeddings]
