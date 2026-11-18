# Unison

> **Slow and steady notification intelligence.**

Unison is a local-first notification aggregation and triage system. It
collects notifications, groups related messages, classifies priority, and
surfaces concise actions without sending notification content to a cloud
service.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/rust-2021-000000?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![Local inference](https://img.shields.io/badge/inference-local%20only-success)](#privacy-and-security)

## What Unison does

- **Ingests** notifications from mock data, JSON file drops, IMAP email, and
  native desktop notification listeners.
- **Deduplicates** related messages with local FastEmbed embeddings and
  LanceDB.
- **Batches** work with a debounce window so local inference is not started for
  every individual event.
- **Triages** notifications with deterministic rules or a structured local
  Ollama model.
- **Presents** digests through a Python dashboard, React web UI, terminal UI,
  or standalone Android application.
- **Adapts** to battery and memory pressure by falling back to deterministic
  Tier-1 rules and releasing non-essential model resources.

## Architecture

```text
                  Native OS notifications
                  Android / email / files
                            |
                            v
                 +-----------------------+
                 | Ingestion adapters    |
                 | Rust bridge / Python  |
                 +-----------+-----------+
                             |
                             v
                 +-----------------------+
                 | Bounded batch window  |
                 | and input validation  |
                 +-----------+-----------+
                             |
              +--------------+--------------+
              |                             |
              v                             v
      FastEmbed + LanceDB             Tier-1 rules
      semantic deduplication           (low resources)
              |                             |
              +--------------+--------------+
                             v
                 +-----------------------+
                 | Local triage engine   |
                 | Ollama + Instructor   |
                 | + Pydantic v2        |
                 +-----------+-----------+
                             |
          +------------------+------------------+
          |                  |                 |
          v                  v                 v
   Python API/dashboard   Rust IPC/TUI   Android app
```

The Rust daemon (`unisond`) is the authoritative local process for the IPC
path. Clients communicate over a local JSON-RPC 2.0 stream:

- Linux/macOS: a Unix domain socket.
- Windows: a named pipe.
- Newline-delimited frames are bounded to 256 KiB.
- Clients request health and snapshots, then subscribe to notification events.
- Slow subscribers are isolated with bounded queues and sequence-gap recovery.

See [`docs/ipc-architecture.md`](docs/ipc-architecture.md) for the protocol
and endpoint security model.

## Repository map

| Directory | Role |
|---|---|
| [`src/`](src/) | Python ingestion, schemas, deduplication, local inference, API, and diagnostics |
| [`daemon/`](daemon/) | Rust `unisond` daemon and notification event fan-out |
| [`ipc/`](ipc/) | Shared Rust JSON-RPC protocol and local transports |
| [`client/`](client/) | Small Rust health-check client for `unisond` |
| [`bridge/`](bridge/) | Windows WinRT and Linux D-Bus notification listener bridge |
| [`tui/`](tui/) | Ratatui terminal dashboard and rule editor |
| [`frontend/`](frontend/) | React 19 + Vite web dashboard |
| [`android/`](android/) | Standalone Kotlin/Compose Android application |
| [`updater/`](updater/) | Signed, rollback-safe Rust update module |
| [`plugins/wasm-host/`](plugins/wasm-host/) | Capability-restricted WebAssembly plugin host |
| [`bench/`](bench/) | Triage accuracy, latency, CPU, and memory benchmarks |
| [`docs/`](docs/) | Architecture, security, diagnostics, and operational notes |
| [`tests/`](tests/) | Python unit and integration tests |

## Local privacy and security

Unison is designed for local execution:

- Notification bodies, contacts, and credentials are not sent to a hosted LLM.
- Model calls use Ollama; structured output is validated with Instructor and
  Pydantic v2.
- IPC endpoints are local-only by default and do not expose a TCP listener.
- Notification text, actions, frames, queues, and event history are bounded.
- The notification bridge uses a bounded channel so OS callbacks never wait for
  inference or database work.
- Diagnostic traces redact notification content and sender fields. Crash dumps
  are encrypted with AES-256-GCM.
- WASM plugins run with restricted capabilities and bounded execution.
- Updates verify HTTPS, Ed25519 signatures, size, SHA-256, and a post-update
  health check before removing the rollback binary.

Read [`docs/diagnostics.md`](docs/diagnostics.md),
[`docs/notification-listener-bridge.md`](docs/notification-listener-bridge.md),
[`docs/auto-updater.md`](docs/auto-updater.md), and
[`docs/wasm-plugins.md`](docs/wasm-plugins.md) for implementation details.

## Python quick start

### Requirements

- Python 3.10 or newer
- Ollama for local LLM triage
- Node.js 18 or newer only when building the React frontend

### Install

```bash
git clone https://github.com/coderForLife-A1/Unison.git
cd Unison

python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
# source .venv/bin/activate

python -m pip install -r requirements.txt
ollama serve
ollama pull qwen2.5:3b
```

Optional configuration is read from environment variables or a local `.env`
file. The main settings are:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Local triage model |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | FastEmbed model |
| `LANCE_DB_PATH` | `.lancedb` | Local vector store |
| `DEDUP_SIMILARITY_THRESHOLD` | `0.85` | Similarity required for grouping |
| `DEDUP_TIME_WINDOW_MINUTES` | `30` | Deduplication time window |
| `BATCH_WINDOW_SECONDS` | `30` | Debounce window before triage |
| `FILE_WATCH_DIR` | `./data/inbox` | JSON notification drop directory |
| `UNISON_BATTERY_SAVER` | unset | Force constrained Tier-1 triage |
| `UNISON_CRASH_DUMP_KEY` | generated locally | URL-safe base64 crash-dump key |

### Run the Python dashboard/API

The default entry point starts the FastAPI server and serves the built React
dashboard when `frontend/dist/` exists:

```bash
python main.py
```

Open [http://localhost:8000](http://localhost:8000). The API includes:

```text
GET    /api/status
GET    /api/summaries
POST   /api/ingest
POST   /api/notifications/direct
DELETE /api/summaries
```

For a local Streamlit dashboard with ingestion controls and filters:

```bash
streamlit run app.py
```

For a one-shot mock ingestion, deduplication, and triage cycle:

```bash
python main.py --cli
```

If Ollama is unavailable, the dashboard/API remains usable and returns an
explicit offline notice instead of attempting a cloud fallback.

### Build the React frontend

```bash
cd frontend
npm install
npm run build
```

Use `npm run dev` for frontend-only development. The generated `dist/`
directory is served by the Python API when present.

## Rust daemon and local IPC

Build the daemon and the health-check client independently:

```bash
cargo build --manifest-path ipc/Cargo.toml
cargo build --manifest-path daemon/Cargo.toml
cargo build --manifest-path client/Cargo.toml
```

Run the daemon in one terminal and query it from another:

```bash
cargo run --manifest-path daemon/Cargo.toml
cargo run --manifest-path client/Cargo.toml
```

The benchmark-only `benchmark.ingest` RPC is disabled by default. Enable it
only for the stress harness:

```powershell
$env:UNISON_BENCHMARK = "1"
cargo run --manifest-path daemon/Cargo.toml
```

The bridge currently supports Windows notification listeners and a Linux
D-Bus notification server. Platform permissions and desktop ownership rules
are intentionally surfaced as explicit failures; Unison does not silently
replace an existing notification daemon.

## Android app

The standalone Android app runs on-device and does not require the Python
server, a laptop, or cloud inference. It uses NotificationListenerService,
Jetpack Compose, Room, and MediaPipe GenAI.

Open [`android/`](android/) in Android Studio, or build from that directory:

```bash
cd android
./gradlew assembleDebug
```

On Windows, use `gradlew.bat assembleDebug`. The debug APK is written to
`android/app/build/outputs/apk/debug/app-debug.apk`.

After installation, grant Unison notification access in Android settings.
Compatible on-device model files are optional; the app has a lightweight
heuristic fallback when no model is installed. See
[`android/README.md`](android/README.md) for device setup.

## Benchmarks and tests

Run the Python tests:

```bash
python -m pytest tests
```

Run the deterministic 200-notification benchmark:

```bash
python bench/benchmark_triage.py --engine rules
```

Run the local Ollama benchmark:

```bash
python bench/benchmark_triage.py --engine ollama --model qwen2.5:3b
```

The benchmark reports accuracy, critical-alert misses, high-priority false
positives, latency percentiles, throughput, CPU time, and RSS memory.

Additional Rust stress and destructive integration harnesses live in
[`bench/`](bench/). Use them only against a disposable local environment.

## Design principles

1. **Private by default:** local inference and local storage.
2. **Slow and steady:** batch and debounce work to reduce resource usage.
3. **Fail explicitly:** permission, protocol, model, and update failures are
   visible rather than silently downgraded.
4. **Bounded everywhere:** input sizes, queues, history, diagnostics, and
   plugin execution have explicit limits.
5. **One source of truth:** the daemon owns authoritative IPC state; clients
   render and issue validated commands.

## License

Unison is distributed under the MIT License. See [`LICENSE`](LICENSE).
