"""Ollama LLM client wrapper for Unison.

Provides a thin abstraction over the ``ollama`` Python package that:
  - Checks whether the Ollama server is reachable.
  - Raises a human-readable ``OllamaOfflineError`` when it is not.
  - Exposes the raw client for downstream use with Instructor.
"""

from __future__ import annotations

import logging

import ollama as _ollama_pkg

from src.config import settings

logger = logging.getLogger(__name__)


class OllamaOfflineError(Exception):
    """Raised when the local Ollama server cannot be reached.

    The error message includes remediation steps so the user knows exactly
    what to do (run ``ollama serve``).
    """

    def __init__(self, host: str | None = None) -> None:
        host = host or settings.ollama_host
        super().__init__(
            f"Ollama is not reachable at {host}. "
            "Please start the Ollama server by running:\n\n"
            "    ollama serve\n\n"
            "Then make sure the model is pulled:\n\n"
            f"    ollama pull {settings.ollama_model}\n"
        )


class OllamaClient:
    """Manages the connection to a local Ollama inference server.

    Parameters
    ----------
    host : str | None
        Override for the Ollama server URL.  Falls back to
        ``settings.ollama_host`` (default ``http://localhost:11434``).
    model : str | None
        Override for the model tag.  Falls back to
        ``settings.ollama_model`` (default ``qwen2.5:3b``).
    """

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
    ) -> None:
        self.host: str = host or settings.ollama_host
        self.model: str = model or settings.ollama_model
        self._client = _ollama_pkg.Client(host=self.host)

    # ── Public API ──────────────────────────────────────────

    def is_available(self) -> bool:
        """Return ``True`` if the Ollama server is running and reachable.

        This performs a lightweight "list models" call.  Any connection
        error is caught and logged; the method **never** raises.
        """
        try:
            self._client.list()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Ollama availability check failed: %s", exc)
            return False

    def get_client(self) -> _ollama_pkg.Client:
        """Return the underlying ``ollama.Client`` instance.

        Raises
        ------
        OllamaOfflineError
            If the server is not reachable at the configured host.
        """
        if not self.is_available():
            raise OllamaOfflineError(self.host)
        return self._client

    def ensure_available(self) -> None:
        """Assert that Ollama is online, raising ``OllamaOfflineError`` if not."""
        if not self.is_available():
            raise OllamaOfflineError(self.host)
