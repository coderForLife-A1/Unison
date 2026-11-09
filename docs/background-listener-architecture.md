# Unison Persistent Background Listener

## 1. Scope and goals

Unison is a local-only notification listener and triage pipeline for Android,
Windows, and Linux. The listener must:

- capture notifications without running model inference on the OS callback thread;
- survive process restarts without losing accepted events;
- remain useful when the embedding/LLM model is unavailable;
- bound CPU, memory, disk, and wakeups;
- make platform limitations visible instead of promising an always-running process.

The system is **best effort at capture and durable at processing**. No desktop or
mobile OS guarantees that an application can run continuously under all power or
memory policies.

The current Android implementation captures events in
[`UnisonNotificationListener`](../android/app/src/main/java/com/unison/app/service/UnisonNotificationListener.kt),
but currently starts inference directly from `onNotificationPosted`. The target
architecture moves that work to a durable local queue.

## 2. Common architecture

```text
OS callback / desktop adapter
        |
        v
Normalizer + idempotency key
        |
        +--> Stage 1 rules (synchronous, bounded)
        |       +--> immediate durable result
        |       +--> persisted pending event
        |
        v
SQLite inbox/outbox
        |
        v
Batch scheduler (debounce + max latency)
        |
        v
Local embedding/model worker
        |
        v
SQLite result + vector metadata + UI event
```

### 2.1 Process boundaries

Use one shared core library and thin platform adapters:

- **Core:** Rust or Kotlin/JVM-compatible state machine, SQLite repository,
  rule engine, queue, retry policy, health state, and model adapter.
- **Android adapter:** `NotificationListenerService`, Room/SQLite worker, and
  optional user-visible foreground service only while a long-running model job
  is actively executing.
- **Windows adapter:** Windows Service hosted by the Service Control Manager
  (SCM), with a separate tray/UI process if needed.
- **Linux adapter:** `systemd` user or system service; desktop UI is a separate
  process.

The OS callback must only normalize, run cheap rules, and commit the event.
It must not load a model, perform network I/O, or wait for a batch flush.

### 2.2 Durable state machine

Every event has one durable state:

```text
RECEIVED -> RULE_CLASSIFIED
RECEIVED -> PENDING_MODEL -> PROCESSING -> TRIAGED
RECEIVED -> SUPPRESSED
PENDING_MODEL/PROCESSING -> RETRY_WAIT
RETRY_WAIT -> PENDING_MODEL
any non-terminal state -> FAILED (after bounded retry budget)
```

The SQLite transaction that inserts an event also inserts its idempotency key.
On restart, the worker claims rows with an expiring lease and resumes
`PENDING_MODEL`, `RETRY_WAIT`, and stale `PROCESSING` rows. A result is emitted
only after the result transaction commits.

Required columns:

- `event_id` and source-provided event key;
- `state`, `attempt_count`, `next_attempt_at`;
- `lease_owner`, `lease_expires_at`;
- `received_at`, `processed_at`, and `last_error`;
- model/ruleset version and embedding model ID.

Use a unique constraint on `(source_application, source_event_key)` when the
source provides a stable key. Otherwise derive a short-lived hash from source,
sender, normalized text, and receipt timestamp.

### 2.3 Resource policy

Default behavior is quiet and conservative:

- Stage 1 runs inline and has a hard execution budget (for example 10 ms).
- Queue capacity is bounded; overflow is persisted and surfaced as degraded
  health, never silently dropped.
- Embedding inference is batched and debounced. Enforce both a maximum batch
  size and maximum event age.
- Allow only one model batch per device by default. Increase concurrency only
  after measuring memory pressure.
- Keep model weights loaded only while the local policy permits it; unload after
  an idle timeout on battery.
- Compact SQLite and prune raw payloads according to an explicit retention
  policy, never from the OS callback.

## 3. Android architecture

### 3.1 Capture mechanism

Use `NotificationListenerService` as the capture adapter. It is the correct
system-mediated API for notifications and is not equivalent to an ordinary
background service. The user must explicitly enable notification access in
system settings.

Implement:

```kotlin
class UnisonNotificationListener : NotificationListenerService() {
    override fun onListenerConnected() { /* mark capture online; drain */ }
    override fun onListenerDisconnected() { /* mark degraded; request bounded rebind */ }
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        // normalize + Stage 1 + durable enqueue; return quickly
    }
}
```

`onNotificationPosted` should write a compact event to Room/SQLite and return.
A `CoroutineScope` owned by the service must be cancelled in `onDestroy`; it
must not launch unbounded work for every notification.

### 3.2 Model execution

Use a worker owned by the application process, not the listener callback:

- `WorkManager` is suitable for persisted retry and deferred model work, but
  periodic work is inexact and must not be used as a real-time listener.
- A foreground service is justified only for user-visible, ongoing processing
  that cannot be completed within normal background limits. It needs a visible
  notification and the correct foreground-service declaration/type for the
  target SDK.
- Do not start a foreground service from arbitrary background callbacks when
  Android’s start restrictions prohibit it. Enqueue work and let an allowed
  lifecycle point start it.

For on-device MediaPipe inference, gate model loading on:

1. local model present and verified;
2. sufficient memory;
3. charging or the user’s battery policy, unless the event is high priority;
4. no active thermal throttling;
5. queue age exceeding the configured maximum.

Stage 1 must still classify OTP and security/system alerts when the model is
unavailable. The current fallback in
[`OnDeviceLlmEngine`](../android/app/src/main/java/com/unison/app/engine/OnDeviceLlmEngine.kt)
should become an explicit `MODEL_UNAVAILABLE` result rather than hiding repeated
model failures as successful semantic results.

### 3.3 Battery optimization and Doze

Android may defer background work during Doze, App Standby, restricted-app
states, and vendor-specific battery managers. Do not treat
`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` as a reliability switch:

- request exemption only when the product’s core capture behavior requires it;
- explain the user-visible trade-off and deep-link to the relevant settings;
- record whether the exemption was granted;
- provide a functioning degraded mode when it was denied.

Notification listener callbacks can still be constrained by process lifecycle
and vendor policy. The app must expose a health screen showing notification
access, battery restriction state, last callback, pending count, and last
successful drain.

### 3.4 Boot, update, and low-memory recovery

Register a `BOOT_COMPLETED` receiver only to schedule reconciliation and
health work. It must not assume that a model can be loaded during boot.

On process recreation:

1. open the database and recover expired leases;
2. run Stage 1 reconciliation over pending events;
3. call `requestRebind(ComponentName(...))` only with bounded exponential backoff
   if the notification listener is disconnected;
4. let the listener reconnect through the platform;
5. drain pending model work under current power/thermal limits.

Do not promise recovery of notifications that were posted while the listener
process was unavailable; Android does not provide a universal historical replay
of every notification. Where the platform supplies active notification state,
reconcile it opportunistically and deduplicate by notification key.

Under low memory, Android may kill the process without calling application
cleanup code. Therefore:

- commit each event before asynchronous work;
- never keep the only copy in an in-memory channel;
- use short leases so killed workers become reclaimable;
- cap model memory and close inference resources after failures;
- record `onTrimMemory` levels and reduce batch/model residency accordingly.

### 3.5 Android manifest and UX checklist

The existing manifest already declares notification-listener access, boot
receive, and foreground service permissions. The implementation must additionally
ensure that declarations match the actual foreground-service type and target SDK.

User-facing controls:

- Enable notification access.
- Battery policy: unrestricted / optimized / restricted.
- Process on battery: never / high-priority only / all.
- Model idle timeout and maximum queue age.
- “Listener health” with remediation actions.

## 4. Windows architecture

### 4.1 Service hosting

Run the listener as a native Windows Service registered with SCM, not as a
hidden startup GUI process or scheduled task. Use a separate tray application
for settings and status because services run in Session 0 and must not display
interactive UI.

The service should:

- report `SERVICE_RUNNING` only after SQLite and the capture adapters are ready;
- respond promptly to stop, shutdown, and preshutdown notifications;
- checkpoint the inbox before stopping;
- expose a local named-pipe or loopback status endpoint with authenticated
  per-user access;
- run with the least-privileged service account that can access its data.

If Windows-specific notification APIs require a per-user process, keep that
capture broker separate from the privileged worker. The broker writes to the
same durable local inbox and reconnects independently.

### 4.2 SCM recovery

Configure automatic delayed start and SCM failure actions:

```text
first failure:  restart service after 5 s
second failure: restart service after 30 s
subsequent:    restart service after 5 min
reset period:  1 day
```

The service must exit non-zero for unrecoverable model/database failures so SCM
can apply the failure action. Do not self-restart in a tight loop. Add a
crash-loop circuit breaker that enters `DEGRADED` mode after repeated failures
and leaves a clear Event Log record.

SCM recovery is not a guarantee against arbitrary termination or host-wide
resource exhaustion. Use a small watchdog/health event only when it provides
additional value; avoid two independent supervisors fighting each other.

### 4.3 Power and sleep

Windows can suspend, throttle, or disconnect user-session components during
sleep, Modern Standby, battery saver, or policy enforcement. The service must
not hold a power request continuously. Instead:

- process on wake or connectivity/power change;
- use a short execution window for model batches;
- prefer charging/AC for large batches;
- use ETW/Event Log and service health timestamps to distinguish “not scheduled”
  from “killed”.

For a notification source that is available only in the user session, reconnect
after logon, unlock, session switch, and resume. Persist events before the
session broker exits.

### 4.4 Windows low-memory behavior

Set a bounded process working set through configuration and model policy rather
than relying on the OS to recover it. On memory-pressure signals:

1. stop admitting large semantic batches;
2. flush and close the model;
3. retain events in SQLite;
4. restart the model worker in-process if safe, otherwise exit with a failure
   status for SCM recovery;
5. reopen the database and reclaim stale leases.

Never delete pending notifications to “solve” memory pressure.

## 5. Linux architecture

### 5.1 systemd service

Ship a user service for desktop installs and optionally a system service for
machine-wide deployments. A user service avoids unnecessary root privileges and
naturally follows the user session.

```ini
[Unit]
Description=Unison local notification listener
After=graphical-session.target
Wants=graphical-session.target

[Service]
ExecStart=%h/.local/bin/unison daemon
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=78
MemoryHigh=384M
MemoryMax=512M
OOMPolicy=kill
KillMode=mixed
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
```

`OOMPolicy=kill` ensures the whole service cgroup is terminated when the
service crosses its memory policy. Combined with `Restart=on-failure`, this
allows systemd to apply the configured recovery policy; verify the exact
behavior on the target distribution because OOM handling and `OOMPolicy`
support vary across systemd versions. Do not select `OOMPolicy=restart`
without testing it against the deployed systemd version.

Add `WatchdogSec` only after implementing `sd_notify(WATCHDOG=1)` correctly.
The watchdog must detect a wedged event loop, not merely restart a busy model.

### 5.2 Sleep, suspend, and desktop session limits

Linux desktop notification APIs and DBus sessions are session-scoped. Reconnect
on DBus restart, desktop session restart, suspend/resume, and network changes.
Do not hold a system inhibitor lock continuously; that would defeat Unison’s
low-power goal. Drain overdue work after resume with rate and batch limits.

If the user explicitly enables a “process while asleep” policy, document that
model work cannot run while the machine is suspended and will run on resume.

### 5.3 OOM and restart recovery

On startup and after every restart:

- run `PRAGMA integrity_check` on a maintenance schedule, not on every event;
- reclaim expired leases;
- move stale `PROCESSING` rows back to `PENDING_MODEL`;
- rebuild only missing vector metadata;
- emit a `daemon_recovered` health event containing the previous exit reason.

Use cgroup limits (`MemoryHigh`, `MemoryMax`, and optionally CPU quotas) to
contain a quantized model. Keep the database outside ephemeral directories and
ensure its directory is writable by the service user.

## 6. Supervisor-independent recovery protocol

The supervisor (Android lifecycle, SCM, or systemd) only restarts the process.
Correctness comes from the database protocol:

```text
startup
  -> acquire single-instance lock
  -> recover expired leases
  -> verify schema
  -> start capture adapter
  -> start bounded worker

event
  -> validate + idempotency check
  -> transactionally insert RECEIVED
  -> run Stage 1
  -> transactionally mark immediate result or PENDING_MODEL

worker
  -> claim N rows with lease
  -> batch/debounce
  -> run local model
  -> commit result and TRIAGED
  -> release/renew lease only while actively processing

shutdown/kill
  -> no cleanup required for correctness
  -> next startup reclaims expired work
```

Lease duration must exceed the normal model batch duration, with renewal
between model phases. Use a stable `worker_id` and transactionally compare
`lease_owner` when committing so a late worker cannot overwrite a newer result.

## 7. Health, observability, and operator controls

Expose the same health contract on all platforms:

- `capture_state`: connected, disconnected, permission_denied, unavailable;
- `last_event_at`, `last_successful_batch_at`, and `last_recovery_at`;
- queue depth, oldest pending age, retry count, and model residency;
- battery/AC/thermal state where available;
- process restart count and last supervisor reason;
- database free space and integrity status.

Alert thresholds should be based on user-visible impact:

- no capture event for a configurable interval while permission is enabled;
- oldest pending event exceeds maximum latency;
- repeated model failures;
- repeated supervisor restarts;
- database write or migration failures.

The UI must provide copyable remediation instructions rather than claiming the
listener is “always on”.

## 8. Acceptance tests

1. Kill the process during each state transition; restart and verify no accepted
   event is lost or processed twice.
2. Fill the queue and verify new events are durably persisted and health reports
   backpressure.
3. Force model load failure; verify Stage 1 continues and pending rows retry.
4. Trigger Android `onTrimMemory`, revoke notification access, enable battery
   restriction, reboot, and resume from Doze.
5. Stop/start the Windows service, terminate it, log off/on, sleep/wake, and
   verify SCM recovery and session broker reconnection.
6. On Linux, test normal exit, crash, SIGKILL, cgroup memory pressure, OOM, DBus
   restart, suspend/resume, and systemd restart.
7. Verify every platform reports the same health states and recovery counters.

## 9. Non-goals and guarantees

Unison guarantees durable processing for events committed to its local inbox and
bounded recovery after process death. It does **not** guarantee delivery while
the OS has disabled notification access, suspended the user session, removed
the application, or terminated a source-specific broker before it could commit
the event.
