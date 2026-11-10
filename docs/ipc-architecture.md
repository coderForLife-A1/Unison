# Unison IPC architecture

## 1. Purpose and process split

Unison runs as two local processes:

```text
OS adapters + ingestion + durable queue + rules + models + storage
                              |
                         unisond
                              |
             local authenticated IPC endpoint
                    /                       \
             unison CLI                  unison TUI
```

`unisond` is the only process allowed to ingest notifications, access the
database, run rules/models, and publish authoritative state. The CLI/TUI is a
thin client: it renders snapshots, sends validated commands, and subscribes to
events. It never opens the database or duplicates triage logic.

The reference implementation is split into [`ipc/`](../ipc),
[`daemon/`](../daemon), and [`client/`](../client). The existing Rust TUI in
[`tui/`](../tui) should depend on `unison-ipc` when its demo producer is
replaced. The client crate demonstrates the same lifecycle with a bounded
health request.

## 2. Transport and endpoint

| Platform | Endpoint | Server | Client |
|---|---|---|---|
| Linux/macOS | `$XDG_RUNTIME_DIR/unison/unison.sock`, falling back to the OS temp directory | Unix domain socket | Unix domain socket |
| Windows | `\\.\pipe\unison` | Named pipe | Named pipe |

The protocol is JSON-RPC 2.0 over newline-delimited UTF-8 JSON. Each frame is
one JSON object and is limited to 256 KiB. Newline framing avoids ambiguous
message boundaries while preserving a debuggable, dependency-light protocol.
Protobuf can be introduced later only with an explicit protocol version; it
must not silently change the JSON contract.

## 3. Authentication and authorization

This is a local capability, not a network service.

### Unix

1. `unisond` creates the runtime directory with mode `0700`.
2. It removes only a stale socket at the exact configured endpoint.
3. It binds the socket and sets mode `0600`.
4. Clients verify the socket has no group/other permission bits before
   connecting. A client refuses a socket with mode `0660`, `0666`, or a
   symlinked/unexpected endpoint.
5. Installations that run under a dedicated service account must instead use a
   group-owned `0660` socket and an explicit allowlisted group; that policy is
   opt-in, not the default.

File permissions authenticate the OS user. Authorization is still checked per
method: read-only clients may call `health`, `get_snapshot`, and `subscribe`;
mutating commands require an authenticated local client and version checks.

### Windows

The daemon creates the named pipe with a security descriptor whose DACL grants
`FILE_GENERIC_READ`, `FILE_GENERIC_WRITE`, and `FILE_CREATE_PIPE_INSTANCE` only
to the current user SID (plus the service account when explicitly configured).
It must not use a permissive “Everyone” ACL. The client cannot infer ACLs from
file metadata, so connection setup must fail closed if secure pipe creation
fails. The production Windows implementation should use `CreateNamedPipeW`
with `SECURITY_ATTRIBUTES`; the Tokio adapter preserves that ACL boundary.

Never expose a TCP listener by default. A future remote mode must require an
explicit configuration switch, TLS/mTLS, and a different authorization policy.

## 4. JSON-RPC contract

Requests:

```json
{"jsonrpc":"2.0","id":1,"method":"health","params":{}}
{"jsonrpc":"2.0","id":2,"method":"get_snapshot","params":{}}
{"jsonrpc":"2.0","id":3,"method":"subscribe","params":{"since_sequence":120}}
```

Responses have exactly one of `result` or `error`:

```json
{"jsonrpc":"2.0","id":1,"result":{"p99_triage_latency_ms":4.2,"suppressed_total":9,"resident_memory_bytes":12345678,"queue_depth":2,"rules_version":7}}
{"jsonrpc":"2.0","id":9,"error":{"code":-32601,"message":"method not found"}}
```

Events are server-to-client notifications without an `id`:

```json
{"jsonrpc":"2.0","method":"notification.received","params":{"sequence":121,"received_at_ms":1794359977000,"source":"SMS","sender":"+123","body_preview":"Your OTP arrived","priority":"High"}}
```

Supported methods:

- `health`: current daemon metrics and readiness.
- `get_snapshot`: bounded notification/rules/metrics snapshot.
- `subscribe`: acknowledge, then stream `notification.received`.
- `validate_rule`: validate without persistence.
- `replace_rules`: optimistic concurrency using `expected_version`.
- `shutdown`: only for an explicitly authorized local operator.

All strings and arrays are bounded before parsing into application state.
Notification bodies are previewed/redacted by the daemon; the TUI does not
receive credentials or unrestricted raw payloads.

## 5. Connection lifecycle

### Daemon

1. Create and secure the endpoint before starting ingestion.
2. Load durable state and publish `health=starting`.
3. Accept connections; spawn one bounded task per client.
4. Apply per-client frame, queue, and subscription limits.
5. On EOF, cancellation, protocol error, or backpressure timeout, close only
   that client and keep the daemon alive.
6. On shutdown, stop accepting, broadcast `daemon.draining`, flush durable
   writes, close clients, remove the Unix socket, and exit.

### Client

1. Connect with a 2-second deadline.
2. Send `health`, then `get_snapshot`.
3. Subscribe from the last received sequence.
4. Keep rendering from the last snapshot while reconnecting with bounded,
   jittered exponential backoff: 100 ms, 250 ms, 1 s, 2 s, 5 s maximum.
5. If an event sequence is not exactly previous + 1, request a snapshot and
   resubscribe from its final sequence.
6. Treat daemon restart as normal; never spin or block keyboard input.
7. On TUI exit, unsubscribe by closing the connection and restore terminal
   state in a cleanup guard.

Requests have a 5-second default deadline; mutating commands use 10 seconds
only when they perform a durable transaction. A stalled client cannot stall
ingestion or other subscribers.

## 6. Event broadcasting and backpressure

`unisond` assigns one monotonically increasing `u64` sequence after the
notification transaction commits. It broadcasts a bounded in-memory event to
subscribers. The durable database remains the source of truth.

Each subscriber has a bounded queue (256 events). If it falls behind, the
daemon drops its live stream and returns a recoverable `sequence_gap` error;
the client requests `get_snapshot` and resumes. It never blocks ingestion to
serve a slow TUI. A reconnecting client can use `since_sequence` for replay
when the daemon's bounded event journal still contains that range; otherwise
the snapshot path is authoritative.

## 7. Failure and observability requirements

- Endpoint exists but authentication fails: fail closed and report a clear
  local error; do not fall back to TCP.
- Malformed/oversized frames: return JSON-RPC `-32600` when possible, then
  close the connection.
- Unknown methods: return `-32601`.
- Invalid params: return `-32602`.
- Daemon unavailable: client shows `Disconnected`, retains the last snapshot,
  and retries with backoff.
- Every disconnect, rejected permission check, sequence gap, and queue
  overflow is counted in daemon metrics and logged without notification bodies.

## 8. Acceptance criteria

- The daemon and client compile independently.
- No database/model work runs in the client.
- Unix sockets are `0700` directory / `0600` socket by default.
- Windows named pipes are created with a current-user-only DACL.
- A notification event is visible in subscribed clients after durable commit.
- Slow/disconnected clients do not block ingestion.
- Sequence gaps cause snapshot recovery.
- Restart and endpoint cleanup are safe and idempotent.
