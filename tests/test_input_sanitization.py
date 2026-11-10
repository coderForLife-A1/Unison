import pytest

from src.engine.intent_dispatcher import (
    IntentExtractor,
    IntentKind,
    IntentPayload,
    NotificationIntent,
    sanitize_body,
)
from src.engine.safe_regex import SafePattern


def test_sanitize_body_strips_ansi_and_truncates_to_two_kibibytes():
    body = "\x1b[31mOTP 482910\x1b[0m " + ("x" * 3000)

    sanitized = sanitize_body(body)

    assert "\x1b" not in sanitized
    assert len(sanitized.encode("utf-8")) <= 2048
    assert IntentExtractor().extract(NotificationIntent(body))[0].value == "482910"


def test_oversized_raw_input_is_rejected_before_regex_processing():
    with pytest.raises(ValueError, match="exceeds 16384"):
        sanitize_body("x" * 16_385)


def test_safe_pattern_rejects_unbounded_input():
    with pytest.raises(ValueError, match="2048"):
        SafePattern("x").search("x" * 2049)


def test_non_url_entities_reject_command_syntax():
    with pytest.raises(ValueError, match="command syntax"):
        IntentPayload(
            kind=IntentKind.MEETING_ID,
            value="TEAM-42;whoami",
            source_text_hash="0" * 64,
        )
