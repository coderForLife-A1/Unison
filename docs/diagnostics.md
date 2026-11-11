# Unison diagnostics and crash dumps

[`src/diagnostics.py`](../src/diagnostics.py) provides a bounded,
thread-safe diagnostic recorder and encrypted crash capture.

## Trace buffer

`DiagnosticRingBuffer` stores structured JSON-compatible events in a rolling
5 MiB byte budget. When the budget is exceeded, oldest events are evicted.
Convenience methods cover the main trace categories:

```python
ring.ipc_call("subscribe", duration_ms=2.4)
ring.classification(duration_ms=8.1, tier="tier-1")
ring.wasm_invocation("priority.wasm", duration_ms=1.2)
ring.state_change("triage", "tier-2", "tier-1")
```

Sensitive fields (`content`, `body`, `title`, sender/contact fields, and
notification aliases) are replaced with a `redacted`, length, and SHA-256
record before entering the buffer. This means the rolling buffer is safe to
include in diagnostics without retaining notification contents.

## Encrypted crash dumps

`CrashDiagnostics.install_exception_hooks()` captures uncaught main-thread and
thread exceptions; `install_asyncio_hook(loop)` captures unhandled asyncio
task exceptions. The application entrypoint installs the main/thread hooks
under `.unison/crashes`.

Each dump contains:

- process reason and scrubbed exception details;
- total/available/used memory and percentage from `psutil`;
- every current thread's ID, name, and formatted stack;
- the last 500 scrubbed ring events.

The payload is encrypted and authenticated with AES-256-GCM. The key is read
from `UNISON_CRASH_DUMP_KEY` as URL-safe base64, or generated once at
`<dump-directory>/crash-dump.key`. The dump format is:

```text
UNISONCD1 || 12-byte nonce || AES-GCM ciphertext+tag
```

On Unix the key and dumps are mode `0600`. On Windows, deployments should
place the directory under a user-private location with an ACL that excludes
other users. If the key is lost, old dumps are intentionally unrecoverable.
The crash handler fails closed rather than writing plaintext diagnostics.
