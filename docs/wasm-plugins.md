# WebAssembly notification plugins

Unison's optional Rust plugin host is in [`plugins/wasm-host`](../plugins/wasm-host).
It loads untrusted `.wasm` modules with Wasmtime and invokes one transform per
notification. This is a local extension point; plugins do not get access to
Unison's Python objects or database.

## Guest ABI

Each module must export:

```text
memory: WebAssembly linear memory
alloc(len: i32, align: i32) -> i32
transform_notification(ptr: i32, len: i32) -> i64
```

The host writes UTF-8 JSON for a `Notification` at the pointer returned by
`alloc`. The transform returns a packed descriptor:

```text
(output_ptr as u64 << 32) | output_len as u64
```

The output bytes must be UTF-8 JSON matching `ActionPayload`. A `dealloc`
export is optional; the host drops the whole Wasmtime store after each call,
which reclaims guest memory.

The data shapes are:

```json
{
  "id": "notification-id",
  "source": "SMS",
  "sender": "+1234567890",
  "content": "Your OTP is 482910",
  "timestamp": "2026-11-11T01:19:37Z"
}
```

```json
{
  "action": "notify",
  "priority": "High",
  "summary": "An OTP was received.",
  "suggested_reply": null
}
```

## Capability and resource policy

- WASI is linked with an empty `WasiCtx`: no preopened directories, inherited
  environment, arguments, stdin, stdout, or stderr.
- No WASI socket/network interfaces are registered. Arbitrary network access is
  denied by default.
- Linear memory is capped at 16 MiB, input and output at 64 KiB, and each call
  has a hard maximum wall-clock budget of 50 ms.
- Wasmtime fuel and epoch interruption provide a second execution guard. A
  timeout or malformed pointer/JSON is an error; it is never converted into a
  successful action.

The host intentionally has no configuration that grants a plugin filesystem or
network access. If a future capability is needed, add a separate, explicit
allowlist and approval boundary rather than exposing ambient WASI.

## Example integration

```rust
use unison_wasm_host::{Notification, Plugin, PluginConfig};

let plugin = Plugin::from_bytes(
    std::fs::read("plugins/priority.wasm")?.as_slice(),
    PluginConfig::default(),
)?;
let action = plugin.transform_notification(&notification)?;
```

Build and test the isolated crate with:

```text
cargo test --manifest-path plugins/wasm-host/Cargo.toml
```
