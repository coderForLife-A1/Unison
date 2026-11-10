//! A small, capability-restricted host for Unison notification transforms.
//!
//! The guest ABI is intentionally language-neutral:
//! - `memory`: exported linear memory
//! - `alloc(len: i32) -> i32`: allocate an input/output buffer
//! - `transform_notification(ptr: i32, len: i32) -> i64`: return a packed
//!   `(output_ptr: u32, output_len: u32)` containing JSON
//! - `dealloc(ptr: i32, len: i32)`: optional buffer cleanup
//!
//! The JSON values are [`Notification`] and [`ActionPayload`]. The host never
//! exposes filesystem, environment, stdio, or socket capabilities to guests.

use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use wasmtime::{
    Config, Engine, Instance, Linker, Memory, Module, Store, StoreLimits, StoreLimitsBuilder,
    TypedFunc,
};
use wasmtime_wasi::{WasiCtx, WasiCtxBuilder};

const CALL_TIMEOUT: Duration = Duration::from_millis(50);
const MAX_INPUT_BYTES: usize = 64 * 1024;
const MAX_OUTPUT_BYTES: usize = 64 * 1024;
const MAX_MEMORY_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Notification {
    pub id: String,
    pub source: String,
    pub sender: String,
    pub content: String,
    pub timestamp: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct ActionPayload {
    pub action: String,
    pub priority: String,
    pub summary: String,
    #[serde(default)]
    pub suggested_reply: Option<String>,
}

/// Configuration is deliberately restrictive. There is no API to add
/// preopened directories or network access in the default host.
#[derive(Clone, Debug)]
pub struct PluginConfig {
    pub max_memory_bytes: usize,
    pub max_input_bytes: usize,
    pub max_output_bytes: usize,
    pub call_timeout: Duration,
}

impl Default for PluginConfig {
    fn default() -> Self {
        Self {
            max_memory_bytes: MAX_MEMORY_BYTES,
            max_input_bytes: MAX_INPUT_BYTES,
            max_output_bytes: MAX_OUTPUT_BYTES,
            call_timeout: CALL_TIMEOUT,
        }
    }
}

struct PluginState {
    wasi: WasiCtx,
    limits: StoreLimits,
}

pub struct Plugin {
    engine: Engine,
    module: Module,
    config: PluginConfig,
}

impl Plugin {
    /// Compile a plugin. Compilation does not grant it any host capabilities.
    pub fn from_bytes(bytes: &[u8], config: PluginConfig) -> Result<Self> {
        validate_config(&config)?;
        let mut engine_config = Config::new();
        engine_config.epoch_interruption(true);
        engine_config.consume_fuel(true);
        let engine = Engine::new(&engine_config).context("creating Wasmtime engine")?;
        let module = Module::new(&engine, bytes).context("compiling plugin module")?;
        Ok(Self {
            engine,
            module,
            config,
        })
    }

    pub fn transform_notification(&self, notification: &Notification) -> Result<ActionPayload> {
        let input = serde_json::to_vec(notification).context("serializing notification")?;
        if input.len() > self.config.max_input_bytes {
            bail!("notification exceeds plugin input limit");
        }

        let wasi = WasiCtxBuilder::new().build();
        let limits = StoreLimitsBuilder::new()
            .memory_size(self.config.max_memory_bytes)
            .instances(1)
            .tables(4)
            .build();
        let mut store = Store::new(&self.engine, PluginState { wasi, limits });
        store.limiter(|state| &mut state.limits);
        store.set_epoch_deadline(1);
        store
            .set_fuel(10_000_000)
            .context("setting plugin fuel limit")?;

        let mut linker = Linker::new(&self.engine);
        // The preview-1 imports are linked against an empty context. No
        // filesystem, stdio, or networking handles exist, and no socket or
        // preview-2 networking interfaces are registered.
        wasmtime_wasi::preview1::add_to_linker_sync(&mut linker, |state: &mut PluginState| {
            &mut state.wasi
        })
        .context("linking restricted WASI")?;
        let instance = linker
            .instantiate(&mut store, &self.module)
            .context("instantiating plugin")?;
        let memory = required_memory(&instance, &mut store)?;
        let alloc = required_alloc(&instance, &mut store)?;
        let transform = instance
            .get_typed_func::<(i32, i32), i64>(&mut store, "transform_notification")
            .context("plugin must export transform_notification(ptr, len) -> i64")?;

        let input_ptr = alloc
            .call(&mut store, (input.len() as i32, 0))
            .context("plugin alloc failed")?;
        write_memory(&mut store, &memory, input_ptr, &input)?;

        let timed_out = Arc::new(AtomicBool::new(false));
        let watchdog_done = Arc::new(AtomicBool::new(false));
        let watchdog_engine = self.engine.clone();
        let watchdog_timeout = self.config.call_timeout;
        let watchdog_timed_out = Arc::clone(&timed_out);
        let watchdog_done_flag = Arc::clone(&watchdog_done);
        let watchdog = thread::spawn(move || {
            thread::sleep(watchdog_timeout);
            if !watchdog_done_flag.load(Ordering::Acquire) {
                watchdog_timed_out.store(true, Ordering::Release);
                watchdog_engine.increment_epoch();
            }
        });

        let started = Instant::now();
        let result = transform.call(&mut store, (input_ptr, input.len() as i32));
        watchdog_done.store(true, Ordering::Release);
        let _ = watchdog.join();

        if timed_out.load(Ordering::Acquire) || started.elapsed() >= self.config.call_timeout {
            bail!("plugin execution exceeded its configured timeout");
        }
        let packed = result.context("plugin transform_notification failed")?;
        let (output_ptr, output_len) = unpack_result(packed)?;
        if output_len > self.config.max_output_bytes {
            bail!("plugin output exceeds plugin output limit");
        }
        let output = read_memory(&store, &memory, output_ptr, output_len)?;
        serde_json::from_slice(&output).context("plugin returned invalid ActionPayload JSON")
    }
}

fn validate_config(config: &PluginConfig) -> Result<()> {
    if config.max_memory_bytes == 0
        || config.max_input_bytes == 0
        || config.max_output_bytes == 0
        || config.call_timeout > CALL_TIMEOUT
    {
        bail!("plugin limits must be non-zero and timeout must not exceed 50 ms");
    }
    Ok(())
}

fn required_memory<T>(instance: &Instance, store: &mut Store<T>) -> Result<Memory> {
    instance
        .get_memory(store, "memory")
        .context("plugin must export linear memory as `memory`")
}

fn required_alloc<T>(
    instance: &Instance,
    store: &mut Store<T>,
) -> Result<TypedFunc<(i32, i32), i32>> {
    instance
        .get_typed_func(store, "alloc")
        .context("plugin must export alloc(len, align) -> ptr")
}

fn write_memory<T>(store: &mut Store<T>, memory: &Memory, ptr: i32, bytes: &[u8]) -> Result<()> {
    let ptr = checked_range(ptr, bytes.len(), memory.data_size(store))?;
    memory
        .write(store, ptr, bytes)
        .context("writing notification into plugin memory")
}

fn read_memory<T>(store: &Store<T>, memory: &Memory, ptr: u32, len: u32) -> Result<Vec<u8>> {
    let ptr = checked_range(ptr as i32, len as usize, memory.data_size(store))?;
    let mut output = vec![0; len as usize];
    memory
        .read(store, ptr, &mut output)
        .context("reading plugin output from memory")?;
    Ok(output)
}

fn checked_range(ptr: i32, len: usize, memory_size: usize) -> Result<usize> {
    if ptr < 0 {
        bail!("plugin returned a negative memory pointer");
    }
    let start = ptr as usize;
    let end = start
        .checked_add(len)
        .ok_or_else(|| anyhow!("plugin memory range overflow"))?;
    if end > memory_size {
        bail!("plugin memory range is outside linear memory");
    }
    Ok(start)
}

fn unpack_result(packed: i64) -> Result<(u32, u32)> {
    if packed < 0 {
        bail!("plugin returned a negative output descriptor");
    }
    let packed = packed as u64;
    Ok(((packed >> 32) as u32, packed as u32))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_capability_expanding_limits() {
        let config = PluginConfig {
            call_timeout: Duration::from_millis(51),
            ..Default::default()
        };
        assert!(validate_config(&config).is_err());
    }

    #[test]
    fn checks_memory_ranges_and_packed_descriptors() {
        assert!(checked_range(-1, 1, 10).is_err());
        assert!(checked_range(9, 2, 10).is_err());
        assert_eq!(unpack_result((7_i64 << 32) | 3).unwrap(), (7, 3));
        assert!(unpack_result(-1).is_err());
    }
}
