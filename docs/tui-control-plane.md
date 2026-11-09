# Unison TUI Control Plane

## Purpose

The TUI is an operator control plane for the local Unison daemon. It is not a
second ingestion or triage engine. The daemon owns notification processing,
metrics, and rule persistence; the TUI subscribes to snapshots/events and sends
validated commands.

The initial implementation uses Rust with `ratatui` and `crossterm` in
[`tui/`](../tui/). It includes a demo event producer so the layout can be
exercised before the daemon IPC adapter is wired in.

## Layout

```text
+--------------------------------------------------------------------------+
| Live metrics: P99 latency | suppressed | RAM                             |
+-----------------------------------------------+--------------------------+
| Incoming notification stream                 | Rules                   |
| source | sender | priority | body            | [x] regex               |
| ...                                           | [ ] regex               |
|                                               |                          |
+-----------------------------------------------+--------------------------+
| status / regex editor                                                     |
+--------------------------------------------------------------------------+
```

### Keyboard contract

- `q`: quit.
- `Up`/`Down`: select a rule.
- `t`: toggle the selected rule.
- `r`: open a bounded regex input editor.
- `Enter`: stage a new rule.
- `Esc`: cancel rule editing.
- `s` (daemon integration): validate and persist the current rule set.

The prototype keeps at most 200 notifications and 32 rules. Text input is
limited to 512 bytes before it reaches the daemon.

## Daemon integration contract

Use a local-only IPC transport:

- Unix domain socket on Linux.
- Named pipe on Windows.
- Unix domain socket or Binder/loopback bridge for Android tooling.

The TUI must authenticate to the local daemon using a per-user capability or
file ACL. It must not expose a TCP listener by default.

Suggested messages:

```rust
enum ControlRequest {
    Subscribe { since_sequence: u64 },
    ReplaceRules { expected_version: u64, rules: Vec<RuleSpec> },
    ValidateRule { pattern: String },
    GetSnapshot,
}

struct RuleSpec {
    id: String,
    pattern: String,
    enabled: bool,
    action: RuleAction,
}

struct MetricsSnapshot {
    p99_triage_latency_ms: f64,
    suppressed_total: u64,
    resident_memory_bytes: u64,
    queue_depth: u64,
    rules_version: u64,
}
```

The daemon returns `RuleValidationError` with a precise index/message for
invalid regex syntax. The TUI never compiles or executes user-provided regexes;
the daemon compiles them in a bounded worker and atomically swaps an immutable
ruleset only after every rule validates.

## Live stream and metrics

The IPC subscriber receives monotonically increasing sequence numbers:

```rust
struct NotificationEvent {
    sequence: u64,
    received_at_ms: i64,
    source: String,
    sender: String,
    body_preview: String,
    priority: String,
}
```

If a sequence gap is detected, the TUI requests `GetSnapshot` rather than
assuming that the stream is complete. Bodies are truncated and redacted by the
daemon before transmission. The TUI uses a bounded ring buffer so a burst
cannot grow its memory without limit.

P99 is computed by the daemon from a rolling window or histogram, not by the
render loop. RAM is the daemon’s resident set, not the TUI’s estimate; label
the source in the UI to avoid misleading operators.

## Rule update safety

1. TUI sends `ValidateRule(pattern)`.
2. Daemon rejects empty, oversized, malformed, or policy-disallowed patterns.
3. Daemon compiles with a regex engine that has bounded execution behavior.
4. Daemon checks the expected rules version to prevent lost updates.
5. Daemon atomically persists the new ruleset and increments the version.
6. TUI receives the committed snapshot or a conflict response.

Rules are data, not code. Never shell out to `grep`, interpolate patterns into
commands, or evaluate them with a backtracking engine against unbounded text.
Use explicit rule count, pattern length, and evaluation-time limits.

## Failure behavior

- IPC disconnect: show `Disconnected`, retain the last snapshot, and reconnect
  with jittered backoff.
- Daemon restart: resubscribe from the last sequence; request a full snapshot
  if the sequence is unavailable.
- Invalid rule: keep the previous active ruleset and show the validation error.
- Slow daemon: keep rendering the last snapshot; never block the render loop.
- Terminal too small: render metrics and status first, then collapse panels.
- Shutdown: restore raw terminal mode and alternate screen in a cleanup guard.

## Acceptance criteria

- Notification events appear without blocking keyboard input.
- Metrics update independently of notification arrival.
- A 50 notifications/sec burst does not exceed the configured ring buffer.
- Invalid regexes cannot replace the active ruleset.
- Concurrent rule edits return a version conflict rather than overwriting a
  newer daemon rule set.
- The terminal is restored after normal exit, input error, or daemon disconnect.
