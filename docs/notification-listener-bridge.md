# Cross-platform notification listener bridge

The Rust bridge in [`bridge/`](../bridge) keeps platform APIs outside the
Unison core. It normalizes events into:

```rust
NotificationEvent {
    platform,
    app_id,
    title,
    body,
    actions,
    received_at_ms,
}
```

All text is bounded to 8 KiB and action buttons to 16 entries. Delivery uses a
bounded Tokio channel (`256` events) and `try_send`; platform callbacks never
wait for triage or database work. A full channel is reported as an error so
the daemon can expose degraded capture rather than silently blocking an OS
callback.

## Windows

The Windows adapter uses WinRT `UserNotificationListener`:

1. `UserNotificationListener::Current()` opens the system listener.
2. `RequestAccessAsync()` requests user permission.
3. Permission must be `Allowed`; all other statuses fail explicitly.
4. `NotificationChanged` starts a Tokio task that reads toast notifications.
5. `AppUserModelId` and generic toast text elements are normalized into
   `NotificationEvent`.

The bridge does not perform inference in the WinRT callback. A production
Windows package must declare the notification-listener capability and surface
the OS permission flow to the user. The WinRT `UserNotificationListener`
projection exposes normalized visual text but not the original toast action XML;
therefore Windows `actions` is intentionally empty instead of treating labels
as executable actions. An app-owned toast producer can provide actions through
a future explicit adapter.

## Linux

The Linux adapter connects to the session bus, claims
`org.freedesktop.Notifications`, and implements `Notify`, `GetCapabilities`, and
`GetServerInformation`. `Notify` calls are normalized immediately and queued
to the daemon. Action arrays use the freedesktop alternating
`id`, `label` convention.

This design is appropriate when Unison is the notification daemon. If another
desktop notification daemon already owns the well-known name, startup fails
explicitly; Unison must not silently replace it or attempt an unrestricted
DBus monitor. A desktop integration can instead add a narrowly scoped portal
or compositor-specific adapter.

## Daemon integration

`unisond` starts the bridge as an independent Tokio task and forwards
normalized events into its existing broadcast stream. Bridge failure does not
bring down IPC clients; health reporting should expose the listener as
degraded and retry with bounded backoff.
