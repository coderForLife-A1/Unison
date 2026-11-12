#!/usr/bin/env python3
"""Stress harness for the benchmark-only ``unisond`` ingestion method.

The daemon must be started with ``UNISON_BENCHMARK=1``. On Unix, for example:

    python bench/stress_unisond.py --daemon-bin target/debug/unisond

The harness sends 50,000 varied notifications over 10 asyncio tasks by
default. SQLite contention is measured with a separate local probe. WASM
overhead is measured only when ``--wasm-command`` is supplied because the
current daemon does not invoke WASM plugins in its ingestion path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional runtime dependency
    psutil = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAEMON = ROOT / "daemon" / "target" / "debug" / (
    "unisond.exe" if os.name == "nt" else "unisond"
)


@dataclass(frozen=True)
class SyntheticNotification:
    notification_id: str
    source: str
    sender: str
    body: str
    sent_at_ns: int


def make_notification(index: int, rng: random.Random) -> SyntheticNotification:
    size = (64, 256, 2048, 8192, 32768)[index % 5]
    complexity = (
        "otp=483920; expires=10m; action=verify",
        "thread=(" + "reply->" * 8 + "review<-)" * 3,
        "pattern: ^(?:error|warn){1,4}\\s+service_[a-z0-9_]+$",
        '{"nested":{"labels":["work","urgent"],"attempt":%d}}' % (index % 7),
        "unicode-safe ASCII payload " + ("#alert " * 12),
    )[index % 5]
    prefix = f"[{index}] {complexity} sender={rng.choice(('SMS', 'Email', 'System', 'WhatsApp'))} "
    body = (prefix + " " + ("notification payload " * ((size // 22) + 1)))[:size]
    return SyntheticNotification(
        notification_id=f"stress-{index:06d}",
        source=("SMS", "Email", "System", "WhatsApp")[index % 4],
        sender=f"synthetic-{index % 97:03d}",
        body=body,
        sent_at_ns=time.perf_counter_ns(),
    )


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        raise ConnectionError("unisond closed the IPC connection")
    return json.loads(line)


async def connect_unix() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if os.name == "nt":
        raise RuntimeError(
            "The Python stdlib has no raw JSON named-pipe client. Run this harness "
            "under WSL, or install a named-pipe adapter and add it here."
        )
    endpoint = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "unison" / "unison.sock"
    deadline = time.monotonic() + 10
    while True:
        try:
            return await asyncio.open_unix_connection(str(endpoint))
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"timed out waiting for {endpoint}") from None
            await asyncio.sleep(0.05)


async def rpc(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    writer.write(
        (json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode()
    )
    await writer.drain()
    response = await read_frame(reader)
    if response.get("error"):
        raise RuntimeError(f"unisond error {response['error']}")
    return response


async def worker(
    worker_id: int,
    start: int,
    stop: int,
    latencies_ms: list[float],
    errors: list[str],
) -> None:
    rng = random.Random(worker_id)
    reader, writer = await connect_unix()
    try:
        for index in range(start, stop):
            notification = make_notification(index, rng)
            started = time.perf_counter_ns()
            try:
                await rpc(
                    reader,
                    writer,
                    index + 1,
                    "benchmark.ingest",
                    {
                        "notification": {
                            "id": notification.notification_id,
                            "source": notification.source,
                            "sender": notification.sender,
                            "body": notification.body,
                            "received_at_ms": notification.sent_at_ns // 1_000_000,
                        }
                    },
                )
                latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            except Exception as exc:  # one failed request is actionable output
                errors.append(f"worker={worker_id} index={index}: {exc}")
                break
    finally:
        writer.close()
        await writer.wait_closed()


def sqlite_probe(workers: int) -> dict[str, float | int]:
    fd, filename = tempfile.mkstemp(prefix="unison-lock-", suffix=".db")
    os.close(fd)
    path = Path(filename)
    lock_waits: list[float] = []
    lock_errors = 0
    try:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE writes (worker INTEGER, value TEXT)")
        connection.commit()
        connection.close()

        def write_batch(worker: int) -> tuple[list[float], int]:
            waits: list[float] = []
            failures = 0
            db = sqlite3.connect(path, timeout=0.25)
            for item in range(50):
                started = time.perf_counter_ns()
                try:
                    db.execute("INSERT INTO writes VALUES (?, ?)", (worker, str(item)))
                    db.commit()
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    failures += 1
                waits.append((time.perf_counter_ns() - started) / 1_000_000)
            db.close()
            return waits, failures

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for waits, failures in pool.map(write_batch, range(workers)):
                lock_waits.extend(waits)
                lock_errors += failures
        return {
            "writes": len(lock_waits) - lock_errors,
            "lock_errors": lock_errors,
            "mean_write_ms": statistics.fmean(lock_waits) if lock_waits else 0.0,
            "p99_write_ms": percentile(lock_waits, 99) if lock_waits else 0.0,
        }
    finally:
        path.unlink(missing_ok=True)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def rss_bytes(process: subprocess.Popen[bytes]) -> int | None:
    if psutil is None:
        return None
    try:
        return psutil.Process(process.pid).memory_info().rss
    except psutil.Error:
        return None


async def rss_sampler(process: subprocess.Popen[bytes], samples: list[dict[str, Any]], stop: asyncio.Event) -> None:
    while not stop.is_set():
        rss = rss_bytes(process)
        if rss is not None:
            samples.append({"elapsed_s": round(time.monotonic(), 3), "rss_bytes": rss})
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


async def run(args: argparse.Namespace) -> dict[str, Any]:
    daemon = Path(args.daemon_bin)
    if not daemon.exists():
        raise RuntimeError(f"daemon binary does not exist: {daemon}")
    env = os.environ.copy()
    env["UNISON_BENCHMARK"] = "1"
    process = subprocess.Popen([str(daemon)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    samples: list[dict[str, Any]] = []
    sampler_stop = asyncio.Event()
    sampler = asyncio.create_task(rss_sampler(process, samples, sampler_stop))
    latencies: list[float] = []
    errors: list[str] = []
    started = time.perf_counter()
    try:
        if args.count % args.workers:
            raise ValueError("--count must be divisible by --workers")
        batch = args.count // args.workers
        await asyncio.gather(
            *(
                worker(worker_id, worker_id * batch, (worker_id + 1) * batch, latencies, errors)
                for worker_id in range(args.workers)
            )
        )
    finally:
        sampler_stop.set()
        await sampler
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    elapsed = time.perf_counter() - started
    result: dict[str, Any] = {
        "notifications_requested": args.count,
        "notifications_completed": len(latencies),
        "virtual_threads": args.workers,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_notifications_per_second": round(len(latencies) / elapsed, 2) if elapsed else 0.0,
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p90": round(percentile(latencies, 90), 3),
            "p99": round(percentile(latencies, 99), 3),
            "histogram": histogram(latencies),
        },
        "sqlite_write_lock_contention": sqlite_probe(args.workers),
        "wasm_runtime_overhead": {"status": "not_available", "reason": "unisond ingestion does not invoke WASM"},
        "rss_samples": samples,
        "errors": errors[:10],
    }
    if args.wasm_command:
        result["wasm_runtime_overhead"] = await wasm_probe(args.wasm_command)
    return result


def histogram(values: list[float]) -> dict[str, int]:
    buckets = ("0-1ms", "1-5ms", "5-10ms", "10-50ms", "50-250ms", "250ms+")
    counts = Counter()
    for value in values:
        counts["0-1ms" if value < 1 else "1-5ms" if value < 5 else "5-10ms" if value < 10 else "10-50ms" if value < 50 else "50-250ms" if value < 250 else "250ms+"] += 1
    return {bucket: counts[bucket] for bucket in buckets}


async def wasm_probe(command: str) -> dict[str, Any]:
    samples: list[float] = []
    payload = json.dumps({"id": "wasm-probe", "source": "System", "sender": "bench", "content": "probe"})
    for _ in range(100):
        started = time.perf_counter_ns()
        process = await asyncio.create_subprocess_shell(command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        await process.communicate((payload + "\n").encode())
        if process.returncode:
            raise RuntimeError(f"WASM command exited with {process.returncode}")
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return {"status": "measured", "samples": len(samples), "mean_ms": statistics.fmean(samples), "p99_ms": percentile(samples, 99)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daemon-bin", default=str(DEFAULT_DAEMON))
    parser.add_argument("--count", type=int, default=50_000)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--wasm-command", help="optional command that transforms one JSON notification from stdin")
    parser.add_argument("--json", action="store_true", help="emit only JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.count <= 0 or args.workers <= 0:
        raise SystemExit("--count and --workers must be positive")
    try:
        result = asyncio.run(run(args))
    except (RuntimeError, ValueError) as exc:
        print(f"stress harness failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=None if args.json else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
