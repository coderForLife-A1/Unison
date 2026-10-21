"""Tests for core Pydantic schemas."""

from datetime import datetime

from src.models.schemas import IngestedNotification, NotificationBatch, UnisonSummary


class TestIngestedNotification:
    """Verify IngestedNotification validation and defaults."""

    def test_creates_with_required_fields(self):
        n = IngestedNotification(
            source="SMS",
            sender="+1234567890",
            content="Your OTP is 482910",
        )
        assert n.source == "SMS"
        assert n.sender == "+1234567890"
        assert n.content == "Your OTP is 482910"
        assert n.id  # auto-generated
        assert isinstance(n.timestamp, datetime)

    def test_rejects_invalid_source(self):
        import pytest

        with pytest.raises(Exception):
            IngestedNotification(
                source="Telegram",  # not in the Literal
                sender="test",
                content="hello",
            )

    def test_all_valid_sources(self):
        for source in ("SMS", "WhatsApp", "Email", "System"):
            n = IngestedNotification(
                source=source,
                sender="test",
                content="hello",
            )
            assert n.source == source


class TestNotificationBatch:
    """Verify NotificationBatch grouping logic."""

    def test_empty_batch_defaults(self):
        batch = NotificationBatch()
        assert batch.notifications == []
        assert batch.source == "Unknown"
        assert batch.primary_sender == "Unknown"

    def test_source_and_sender_extraction(self):
        n1 = IngestedNotification(source="WhatsApp", sender="Alice", content="Hi")
        n2 = IngestedNotification(source="WhatsApp", sender="Alice", content="Hello")
        n3 = IngestedNotification(source="SMS", sender="Bob", content="Hey")

        batch = NotificationBatch(notifications=[n1, n2, n3])
        assert batch.source == "WhatsApp"  # most frequent
        assert batch.primary_sender == "Alice"  # most frequent


class TestUnisonSummary:
    """Verify UnisonSummary schema."""

    def test_creates_valid_summary(self):
        s = UnisonSummary(
            source="WhatsApp - Family Group",
            priority="High",
            category="Social",
            summary="Mom is planning Sunday lunch at 1 PM.",
            action_required=True,
            suggested_reply="I'll be there!",
        )
        assert s.priority == "High"
        assert s.action_required is True
        assert s.suggested_reply == "I'll be there!"

    def test_suggested_reply_optional(self):
        s = UnisonSummary(
            source="System",
            priority="Noise",
            category="System",
            summary="Software update available.",
            action_required=False,
        )
        assert s.suggested_reply is None

    def test_rejects_invalid_priority(self):
        import pytest

        with pytest.raises(Exception):
            UnisonSummary(
                source="test",
                priority="Critical",  # not in the Literal
                category="test",
                summary="test",
                action_required=False,
            )

