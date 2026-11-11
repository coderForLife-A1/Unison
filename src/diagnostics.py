"""Bounded structured diagnostics and encrypted crash dumps for Unison."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Callable, Mapping

import psutil
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_BUFFER_BYTES = 5 * 1024 * 1024
MAX_CRASH_EVENTS = 500
KEY_BYTES = 32
NONCE_BYTES = 12
SENSITIVE_KEYS = frozenset(
    {
        "body",
        "content",
        "notification",
        "notification_body",
        "raw_content",
        "title",
        "message",
        "sender",
        "email",
        "phone",
    }
)


def _scrub(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        text = str(value)
        return {
            "redacted": True,
            "length": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {str(k): _scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class DiagnosticRingBuffer:
    """Thread-safe byte-bounded rolling buffer of JSON trace events."""

    def __init__(self, max_bytes: int = MAX_BUFFER_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._events: deque[tuple[int, dict[str, Any]]] = deque()
        self._size = 0
        self._lock = threading.RLock()

    def record(
        self,
        event_type: str,
        *,
        level: str = "INFO",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "level": level,
            "data": _scrub(data or {}),
        }
        encoded_size = len(json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode())
        if encoded_size > self._max_bytes:
            event["data"] = {"truncated": True, "event_size_bytes": encoded_size}
            encoded_size = len(
                json.dumps(event, separators=(",", ":"), ensure_ascii=True).encode()
            )
        with self._lock:
            self._events.append((encoded_size, event))
            self._size += encoded_size
            while self._events and self._size > self._max_bytes:
                size, _ = self._events.popleft()
                self._size -= size

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if limit is not None:
            events = events[-limit:]
        return [event for _, event in events]

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size

    def ipc_call(self, method: str, duration_ms: float, **fields: Any) -> None:
        self.record("ipc_call", data={"method": method, "duration_ms": duration_ms, **fields})

    def classification(self, duration_ms: float, tier: str, **fields: Any) -> None:
        self.record(
            "classification",
            data={"duration_ms": duration_ms, "tier": tier, **fields},
        )

    def wasm_invocation(self, module: str, duration_ms: float, **fields: Any) -> None:
        self.record(
            "wasm_invocation",
            data={"module": module, "duration_ms": duration_ms, **fields},
        )

    def state_change(self, component: str, old: str, new: str, **fields: Any) -> None:
        self.record(
            "state_change",
            data={"component": component, "old": old, "new": new, **fields},
        )


class CrashDiagnostics:
    """Writes encrypted, scrubbed diagnostics when an exception escapes."""

    def __init__(
        self,
        directory: Path,
        *,
        key_path: Path | None = None,
        ring: DiagnosticRingBuffer | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.key_path = key_path or self.directory / "crash-dump.key"
        self.ring = ring or DiagnosticRingBuffer()
        self._handling = threading.Lock()
        self._previous_excepthook: Callable[..., Any] | None = None
        self._previous_thread_hook: Callable[..., Any] | None = None

    def install_exception_hooks(self) -> None:
        self._previous_excepthook = sys.excepthook
        self._previous_thread_hook = threading.excepthook

        def excepthook(
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_traceback: TracebackType | None,
        ) -> None:
            self.handle_exception(exc_type, exc_value, exc_traceback)
            if self._previous_excepthook:
                self._previous_excepthook(exc_type, exc_value, exc_traceback)

        def thread_hook(args: Any) -> None:
            self.handle_thread_exception(args)
            if self._previous_thread_hook:
                self._previous_thread_hook(args)

        sys.excepthook = excepthook
        threading.excepthook = thread_hook

    def install_asyncio_hook(self, loop: Any) -> None:
        loop.set_exception_handler(self.handle_asyncio_exception)

    def handle_thread_exception(self, args: Any) -> None:
        self.handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    def handle_asyncio_exception(self, loop: Any, context: Mapping[str, Any]) -> None:
        error = context.get("exception")
        if error is not None:
            self.handle_exception(type(error), error, error.__traceback__)
        else:
            self.write_crash_dump(
                "asyncio-unhandled",
                None,
                None,
                {"message": str(context.get("message", "unhandled asyncio exception"))},
            )

    def handle_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.write_crash_dump(
            "unhandled-exception",
            exc_type,
            exc_value,
            {"traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))},
        )

    def write_crash_dump(
        self,
        reason: str,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        extra: Mapping[str, Any] | None = None,
    ) -> Path | None:
        if not self._handling.acquire(blocking=False):
            return None
        try:
            payload = {
                "format": 1,
                "reason": reason,
                "exception": {
                    "type": exc_type.__name__ if exc_type else None,
                    "message": _scrub(str(exc_value)) if exc_value else None,
                },
                "system_memory": _memory_status(),
                "threads": _thread_stacks(),
                "events": self.ring.snapshot(MAX_CRASH_EVENTS),
                "extra": _scrub(extra or {}),
            }
            return self._write_encrypted(payload)
        except Exception:
            return None
        finally:
            self._handling.release()

    def _write_encrypted(self, payload: Mapping[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        key = self._load_or_create_key()
        nonce = secrets.token_bytes(NONCE_BYTES)
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, b"unison-crash-dump-v1")
        path = self.directory / (
            f"crash-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.enc"
        )
        with path.open("xb") as file:
            file.write(b"UNISONCD1" + nonce + ciphertext)
        _restrict_file(path)
        return path

    def _load_or_create_key(self) -> bytes:
        configured = os.getenv("UNISON_CRASH_DUMP_KEY")
        if configured:
            key = base64.urlsafe_b64decode(configured.encode())
            if len(key) != KEY_BYTES:
                raise ValueError("UNISON_CRASH_DUMP_KEY must decode to 32 bytes")
            return key
        if self.key_path.exists():
            key = self.key_path.read_bytes()
        else:
            key = secrets.token_bytes(KEY_BYTES)
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            with self.key_path.open("xb") as file:
                file.write(key)
            _restrict_file(self.key_path)
        if len(key) != KEY_BYTES:
            raise ValueError("crash dump key file must contain 32 bytes")
        return key


def _memory_status() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "total": memory.total,
        "available": memory.available,
        "used": memory.used,
        "percent": memory.percent,
    }


def _thread_stacks() -> list[dict[str, Any]]:
    frames: dict[int, FrameType] = sys._current_frames()
    threads = {thread.ident: thread for thread in threading.enumerate()}
    return [
        {
            "thread_id": thread_id,
            "name": threads.get(thread_id).name if threads.get(thread_id) else "unknown",
            "stack": "".join(traceback.format_stack(frame)),
        }
        for thread_id, frame in frames.items()
    ]


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
