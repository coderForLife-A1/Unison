import subprocess

import pytest

from src.engine.intent_dispatcher import (
    IntentExtractor,
    IntentKind,
    NotificationIntent,
    SafeEventDispatcher,
    ShellHook,
)


def test_extracts_supported_intents():
    body = (
        "Use OTP 482 910. Join meeting ID TEAM-42. "
        "Review PR #123 at https://github.com/acme/app/pull/123."
    )
    payloads = IntentExtractor().extract(NotificationIntent(body))
    assert {(payload.kind, payload.value) for payload in payloads} == {
        (IntentKind.OTP, "482910"),
        (IntentKind.MEETING_ID, "TEAM-42"),
        (IntentKind.PULL_REQUEST, "123"),
        (IntentKind.DEEP_LINK, "https://github.com/acme/app/pull/123"),
    }


def test_rejects_unsafe_urls_and_sanitizes_control_chars():
    payloads = IntentExtractor().extract(NotificationIntent(
        "Open javascript://evil.test/%0Aecho pwned or "
        "https://example.test/a;rm -rf /.\x00"
    ))
    assert [payload.kind for payload in payloads] == [IntentKind.DEEP_LINK]
    assert payloads[0].url == "https://example.test/a;rm"

    with pytest.raises(ValueError):
        IntentExtractor().extract(NotificationIntent("x" * 16_385))


def test_dispatches_fixed_argv_without_shell(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = IntentExtractor().extract(
        NotificationIntent("Your OTP is 482910;")
    )[0]
    dispatcher = SafeEventDispatcher({
        IntentKind.OTP: ShellHook("unison-hook", ("otp-handler",)),
    })
    assert dispatcher.dispatch(payload)
    assert calls[0][0] == ["unison-hook", "otp-handler"]
    assert calls[0][1]["shell"] is False
    assert b"482910" in calls[0][1]["input"]


def test_notification_text_cannot_change_executable():
    with pytest.raises(ValueError):
        ShellHook("trusted-hook\n--bad")
