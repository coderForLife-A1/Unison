"""Strict notification intent extraction and safe action dispatch.

Notification text is untrusted input. Extracted values are data only: they
cannot select an executable, alter shell syntax, or become a shell command.
"""

from __future__ import annotations

import json
import subprocess
import unicodedata
import webbrowser
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.engine.safe_regex import SafePattern

MAX_BODY_LENGTH = 16_384
MAX_SANITIZED_BODY_BYTES = 2 * 1024
MAX_FIELD_LENGTH = 512
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
CONTROL_CHARS = SafePattern(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", max_input_bytes=MAX_BODY_LENGTH
)
ANSI_SEQUENCES = SafePattern(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])",
    max_input_bytes=MAX_BODY_LENGTH,
)
OTP_PATTERN = SafePattern(
    r"(?i)\b(?:otp|one[- ]time password|verification code|security code)"
    r"\D{0,24}([0-9](?:[\s-]?[0-9]){3,11})\b"
)
URL_PATTERN = SafePattern(r"(?i)\bhttps?://[^\s<>\x00-\x1f]+")
MEETING_PATTERN = SafePattern(
    r"(?i)\b(?:meeting|conference|event)\s*(?:id|code)?\s*[:#-]?\s*"
    r"([A-Z0-9][A-Z0-9._-]{3,63})\b"
)
PR_PATTERN = SafePattern(
    r"(?i)(?:pull request|pr)\s*#?\s*(\d{1,9})\b"
    r"|https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+/pull/(\d{1,9})(?:[/?#\s]|$)"
)
OTP_SEPARATOR_PATTERN = SafePattern(r"[\s-]")
COMMAND_INJECTION_CHARS = frozenset(";&|`$><")


class IntentKind(str, Enum):
    OTP = "otp"
    DEEP_LINK = "deep_link"
    MEETING_ID = "meeting_id"
    PULL_REQUEST = "pull_request"


class IntentPayload(BaseModel):
    """Validated, structured data extracted from an untrusted body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: IntentKind
    value: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str | None = None

    @field_validator("value")
    @classmethod
    def reject_controls(cls, value: str) -> str:
        if CONTROL_CHARS.search(value) or "\n" in value or "\r" in value:
            raise ValueError("intent value contains control characters")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
            raise ValueError("only http and https URLs are dispatchable")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL must have a host and no credentials")
        if any(ord(char) < 0x20 for char in value):
            raise ValueError("URL contains control characters")
        return value

    @model_validator(mode="after")
    def reject_command_syntax(self) -> "IntentPayload":
        if self.kind is not IntentKind.DEEP_LINK and any(
            character in COMMAND_INJECTION_CHARS for character in self.value
        ):
            raise ValueError("intent value contains command syntax")
        return self


@dataclass(frozen=True)
class NotificationIntent:
    """Input to the extractor; body remains untrusted throughout."""

    body: str
    source: str = "unknown"


def sanitize_body(body: str) -> str:
    """Normalize text and cap the expensive parser input at 2 KiB."""
    if not isinstance(body, str):
        raise TypeError("notification body must be a string")
    normalized = unicodedata.normalize("NFKC", body).replace("\u2028", " ").replace("\u2029", " ")
    if len(normalized) > MAX_BODY_LENGTH:
        raise ValueError(f"notification body exceeds {MAX_BODY_LENGTH} characters")
    without_ansi = ANSI_SEQUENCES.sub("", normalized)
    cleaned = CONTROL_CHARS.sub("", without_ansi)
    encoded = cleaned.encode("utf-8")
    if len(encoded) > MAX_SANITIZED_BODY_BYTES:
        cleaned = encoded[:MAX_SANITIZED_BODY_BYTES].decode("utf-8", errors="ignore")
    return cleaned.strip()


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class IntentExtractor:
    """Extracts only known intent shapes using bounded regular expressions."""

    def extract(self, notification: NotificationIntent) -> list[IntentPayload]:
        body = sanitize_body(notification.body)
        text_hash = _hash_text(body)
        results: list[IntentPayload] = []

        otp_match = OTP_PATTERN.search(body)
        if otp_match:
            otp = OTP_SEPARATOR_PATTERN.sub("", otp_match.group(1))
            if 4 <= len(otp) <= 12:
                results.append(IntentPayload(
                    kind=IntentKind.OTP, value=otp, source_text_hash=text_hash,
                ))

        for match in URL_PATTERN.finditer(body):
            candidate = match.group(0).rstrip(".,;:!?)]}")
            try:
                results.append(IntentPayload(
                    kind=IntentKind.DEEP_LINK,
                    value=candidate,
                    url=candidate,
                    source_text_hash=text_hash,
                ))
            except ValueError:
                continue

        meeting_match = MEETING_PATTERN.search(body)
        if meeting_match:
            results.append(IntentPayload(
                kind=IntentKind.MEETING_ID,
                value=meeting_match.group(1),
                source_text_hash=text_hash,
            ))

        pr_match = PR_PATTERN.search(body)
        if pr_match:
            pr_number = pr_match.group(1) or pr_match.group(2)
            results.append(IntentPayload(
                kind=IntentKind.PULL_REQUEST,
                value=pr_number,
                source_text_hash=text_hash,
            ))
        return results


@dataclass(frozen=True)
class ShellHook:
    """A fixed executable and argv prefix registered by trusted application code."""

    executable: str
    argv_prefix: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.executable or any("\x00" in part or "\n" in part or "\r" in part
                                      for part in (self.executable, *self.argv_prefix)):
            raise ValueError("shell hook contains invalid executable arguments")


class SafeEventDispatcher:
    """Routes validated intents to native URL actions or fixed argv hooks.

    Hook payloads are sent as JSON on stdin. Notification text never selects
    the executable and is never interpolated into a shell command.
    """

    def __init__(
        self,
        hooks: Mapping[IntentKind, ShellHook] | None = None,
        native_url_opener: Callable[[str], bool] | None = None,
    ) -> None:
        self._hooks = dict(hooks or {})
        self._open_url = native_url_opener or webbrowser.open

    def dispatch(self, payload: IntentPayload, timeout_seconds: float = 3.0) -> bool:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if payload.kind is IntentKind.DEEP_LINK:
            if payload.url is None:
                raise ValueError("deep-link payload is missing URL")
            return bool(self._open_url(payload.url))

        hook = self._hooks.get(payload.kind)
        if hook is None:
            return False
        wire_payload = json.dumps(
            payload.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        completed = subprocess.run(
            [hook.executable, *hook.argv_prefix],
            input=wire_payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
        return completed.returncode == 0
