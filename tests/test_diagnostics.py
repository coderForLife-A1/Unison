import base64
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.diagnostics import CrashDiagnostics, DiagnosticRingBuffer


def test_ring_buffer_is_byte_bounded_and_scrubs_notification_content():
    ring = DiagnosticRingBuffer(max_bytes=500)
    ring.record("ipc_call", data={"method": "subscribe", "body": "secret message"})
    for index in range(20):
        ring.state_change("engine", str(index), str(index + 1))

    events = ring.snapshot()
    assert ring.size_bytes <= 500
    assert all("secret message" not in json.dumps(event) for event in events)


def test_crash_dump_is_encrypted_and_contains_scrubbed_recent_events(tmp_path):
    key = b"k" * 32
    key_path = tmp_path / "key"
    key_path.write_bytes(key)
    ring = DiagnosticRingBuffer()
    ring.record("classification", data={"content": "private notification"})
    diagnostics = CrashDiagnostics(tmp_path / "dumps", key_path=key_path, ring=ring)

    dump_path = diagnostics.write_crash_dump("test", None, None)

    assert dump_path is not None
    raw = dump_path.read_bytes()
    assert raw.startswith(b"UNISONCD1")
    assert b"private notification" not in raw
    nonce = raw[9:21]
    decrypted = AESGCM(key).decrypt(nonce, raw[21:], b"unison-crash-dump-v1")
    payload = json.loads(decrypted)
    assert payload["reason"] == "test"
    assert payload["events"][0]["data"]["content"]["redacted"] is True


def test_environment_key_is_supported(tmp_path, monkeypatch):
    key = b"z" * 32
    monkeypatch.setenv("UNISON_CRASH_DUMP_KEY", base64.urlsafe_b64encode(key).decode())
    diagnostics = CrashDiagnostics(tmp_path)
    path = diagnostics.write_crash_dump("test", None, None)
    assert path is not None
    assert not (tmp_path / "crash-dump.key").exists()
