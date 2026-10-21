"""Structured LLM unison engine powered by Ollama and Instructor.

Produces strictly validated `UnisonSummary` objects from incoming
`NotificationBatch` items using local inference.
"""

from __future__ import annotations

import logging
from typing import Optional

import instructor
from pydantic import BaseModel, Field

from src.config import settings
from src.engine.llm_client import OllamaClient, OllamaOfflineError
from src.models.schemas import NotificationBatch, UnisonSummary

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Unison, a quiet, privacy-first notification intelligence assistant.
Your job is to unison notification batches and summarize them into a single actionable digest.

Guidelines:
1. Priority:
   - "High": Urgent action required, important human messages, deadlines, OTPs needed right now.
   - "Low": Informational updates, non-urgent work/personal messages, newsletters worth skimming.
   - "Noise": Marketing/promotional blasts, redundant app pings, spam, social chatter.
2. Category:
   - Work, Personal, Social, OTP, Promo, System, Financial, etc.
3. Summary:
   - Exactly ONE concise, actionable sentence capturing the essence.
4. action_required:
   - True if the user specifically needs to reply, decide, or act; False otherwise.
5. suggested_reply:
   - If action_required is True and a quick reply makes sense, provide a polite, natural 1-sentence draft reply. Otherwise, leave null.
"""


class UnisonEngine:
    """Processes notification batches through Ollama with Instructor structured parsing."""

    def __init__(self, llm_client: Optional[OllamaClient] = None) -> None:
        self.llm_client = llm_client or OllamaClient()
        self._instructor_client = None

    def _get_client(self):
        """Lazy-initialize the Instructor-patched client using Ollama's local OpenAI API."""
        if self._instructor_client is None:
            import openai
            # Ollama exposes an OpenAI-compatible endpoint at /v1 locally on the machine
            host_url = self.llm_client.host.rstrip("/")
            base_url = f"{host_url}/v1" if not host_url.endswith("/v1") else host_url
            raw_client = openai.OpenAI(
                base_url=base_url,
                api_key="ollama",  # Ollama requires a non-empty string, no actual key needed
            )
            self._instructor_client = instructor.from_openai(
                raw_client,
                mode=instructor.Mode.JSON,
            )
        return self._instructor_client

    async def unison_batch(self, batch: NotificationBatch) -> UnisonSummary:
        """Unison a single notification batch into a structured UnisonSummary.

        Parameters
        ----------
        batch : NotificationBatch
            The batch of deduplicated/grouped notifications.

        Returns
        -------
        UnisonSummary
            Strictly validated summary model.
        """
        if not self.llm_client.is_available():
            raise OllamaOfflineError(self.llm_client.host)

        client = self._get_client()

        user_content = (
            f"Source: {batch.source}\n"
            f"Primary Sender: {batch.primary_sender}\n"
            f"Message Count: {len(batch.notifications)}\n"
            f"Payload Content:\n{batch.representative_content}\n"
        )

        try:
            # instructor with ollama client uses chat.completions.create or direct call
            response = client.chat.completions.create(
                model=self.llm_client.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_model=UnisonSummary,
            )
            # Ensure batch_id is set
            response.batch_id = batch.batch_id
            return response
        except Exception as e:
            logger.error("Error during LLM unison for batch %s: %s", batch.batch_id, e)
            # Fallback deterministic summary when local model fails to parse
            return UnisonSummary(
                source=f"{batch.source} - {batch.primary_sender}",
                priority="Low",
                category="General",
                summary=f"Batch of {len(batch.notifications)} notifications received.",
                action_required=False,
                suggested_reply=None,
                batch_id=batch.batch_id,
            )

    async def unison_batches(self, batches: list[NotificationBatch]) -> list[UnisonSummary]:
        """Unison multiple batches sequentially to minimize resource overhead.

        Parameters
        ----------
        batches : list[NotificationBatch]
            List of batches to process.

        Returns
        -------
        list[UnisonSummary]
            List of structured summaries.
        """
        summaries: list[UnisonSummary] = []
        for batch in batches:
            summary = await self.unison_batch(batch)
            summaries.append(summary)
        return summaries

