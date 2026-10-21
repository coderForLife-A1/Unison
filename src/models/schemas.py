"""Core Pydantic v2 schemas for Unison.

Defines the canonical data models flowing through the pipeline:
  IngestedNotification → (dedup) → NotificationBatch → (unison) → UnisonSummary
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class IngestedNotification(BaseModel):
    """A single raw notification ingested from any source adapter."""

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for the incoming message",
    )
    source: Literal["SMS", "WhatsApp", "Email", "System"] = Field(
        ...,
        description="Originating communication channel",
    )
    sender: str = Field(
        description="Sender name, phone number, or email address",
    )
    content: str = Field(
        description="Raw text payload of the notification",
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the notification was received",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "a1b2c3d4",
                "source": "WhatsApp",
                "sender": "Family Group",
                "content": "Mom: Are we still meeting Sunday at 1 PM?",
                "timestamp": "2026-10-21T12:30:00",
            }
        }
    }


class NotificationBatch(BaseModel):
    """A group of semantically related notifications, post-dedup.

    After the dedup engine groups similar messages, they are bundled
    into a batch that gets sent to the LLM unison engine as a single unit.
    """

    batch_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique ID for this deduplicated batch",
    )
    notifications: list[IngestedNotification] = Field(
        default_factory=list,
        description="All notifications grouped into this batch",
    )
    representative_content: str = Field(
        default="",
        description="Merged/representative text for the batch, used for LLM input",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="When this batch was assembled",
    )

    @property
    def source(self) -> str:
        """Return the dominant source channel for this batch."""
        if not self.notifications:
            return "Unknown"
        sources = [n.source for n in self.notifications]
        return max(set(sources), key=sources.count)

    @property
    def primary_sender(self) -> str:
        """Return the most frequent sender in this batch."""
        if not self.notifications:
            return "Unknown"
        senders = [n.sender for n in self.notifications]
        return max(set(senders), key=senders.count)


class UnisonSummary(BaseModel):
    """Structured unison output produced by the LLM engine.

    This is what the Streamlit dashboard ultimately renders.
    Strictly typed via Instructor to guarantee valid JSON from Ollama.
    """

    source: str = Field(
        description="Source channel and sender, e.g. 'WhatsApp - Family Group'",
    )
    priority: Literal["High", "Low", "Noise"] = Field(
        description="Priority classification for unison",
    )
    category: str = Field(
        description="Topic tag, e.g. Work, Personal, OTP, Social, Promo",
    )
    summary: str = Field(
        description="1-sentence actionable summary of the notification batch",
    )
    action_required: bool = Field(
        description="True if the user needs to respond or take action",
    )
    suggested_reply: Optional[str] = Field(
        default=None,
        description="Optional quick draft reply the user can send",
    )
    batch_id: Optional[str] = Field(
        default=None,
        description="ID of the NotificationBatch this summary was generated from",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="When this summary was generated",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "source": "WhatsApp - Family Group",
                "priority": "High",
                "category": "Social",
                "summary": "Mom and Dad are planning Sunday lunch at 1 PM.",
                "action_required": True,
                "suggested_reply": "Sounds good, I'll be there!",
            }
        }
    }

