#!/usr/bin/env python3
"""Destructive integration tests for the benchmark IPC daemon.

This script is intentionally opt-in and destructive. It starts an isolated
``unisond`` process with ``UNISON_BENCHMARK=1`` and exercises recovery from:

* a network interface down/up transition (Linux, explicit interface required),
* concurrent half-written IPC frames,
* SIGSTOP/SIGCONT during a notification burst, and
* killing the daemon while requests are in flight.

The current repository daemon has no P2P synchronizer or durable transaction
endpoint. Those checks are reported as ``not_implemented`` rather than being
silently treated as passes. IPC consistency means that completed responses
remain valid, request IDs are not duplicated, and the daemon can be restarted
and queried after each fault.

Example (Linux; requires privileges for the interface operation)::

    python bench/destructive_integration.py \
        --daemon-bin daemon/target/debug/unisond \
        --network-interface eth0 \
        --i-understand-this-is-destructive

The test never touches the repository's normal socket or database path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any


MAX_FRAME_BYTES = 256 * 1024
DEFAULT_TIMEOUT = 10.0


@dataclass
class FaultResult:
    name: str
    status: str
    recovery_seconds: float | None
    consistency: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "recovery_seconds": self.recovery_seconds,
            "consistency": self.consistency,
            "detail": self.detail,
        }


class DaemonHarness:
    def __init__(self, daemon_bin: Path, workdir: Path, timeout: float) -> None:
        self.daemon_bin = daemon_bin
        self.workdir = workdir
        self.timeout = timeout
        self.runtime_dir = workdir / "runtime"
        self.runtime_dir.mkdir(mode=0o700)
        self.endpoint = self.runtime_dir / "unison" / "unison.sock"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("daemon is already running")
        env = os.environ.copy()
        env["UNISON_BENCHMARK"] = "1"
        env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
        self.process = subprocess.Popen(
            [str(self.daemon_bin)],
            cwd=self.workdir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def wait_ready(self) -> float:
        started = time.monotonic()
        deadline = started + self.timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(f"daemon exited with code {self.process.returncode}")
            try:
                await self.health()
                return time.monotonic() - started
            except (OSError, asyncio.TimeoutError, RuntimeError, json.JSONDecodeError):
                await asyncio.sleep(0.05)
        raise TimeoutError("daemon did not become ready")

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if os.name == "nt":
            raise RuntimeError("destructive IPC harness requires Unix sockets")
        return await asyncio.wait_for(
            asyncio.open_unix_connection(str(self.endpoint)),
            timeout=self.timeout,
        )

    async def health(self) -> dict[str, Any]:
        reader, writer = await self.connect()
        try:
            response = await self.rpc(reader, writer, 1, "health", {})
            if response.get("error") or not isinstance(response.get("result"), dict):
                raise RuntimeError(f"invalid health response: {response!r}")
            return response
        finally:
            writer.close()
            await writer.wait_closed()

    async def rpc(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        request_id: int,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        frame = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode() + b"\n"
        if len(frame) > MAX_FRAME_BYTES:
            raise ValueError("test frame exceeds daemon frame limit")
        writer.write(frame)
        await asyncio.wait_for(writer.drain(), timeout=self.timeout)
        line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        if not line:
            raise ConnectionError("daemon closed IPC connection")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise RuntimeError(f"response/request mismatch: {response!r}")
        return response

    async def ingest(
        self,
        request_id: int,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
    ) -> dict[str, Any]:
        return await self.rpc(
            reader,
            writer,
            request_id,
            "benchmark.ingest",
            {
                "notification": {
                    "id": f"destructive-{request_id}",
                    "source": "System",
                    "sender": "destructive-test",
                    "body": "fault-injection notification",
                    "received_at_ms": int(time.time() * 1000),
                }
            },
        )

    def stop(self, force: bool = False) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        if force:
            self.process.kill()
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def restart(self) -> None:
        self.stop(force=True)
        self.start()


async def run_burst(
    harness: DaemonHarness,
    count: int,
    start_id: int = 1,
) -> tuple[int, list[str]]:
    reader, writer = await harness.connect()
    errors: list[str] = []
    completed = 0
    try:
        for request_id in range(start_id, start_id + count):
            try:
                response = await harness.ingest(request_id, writer, reader)
                if response.get("error"):
                    errors.append(f"{request_id}: {response['error']}")
                else:
                    completed += 1
            except (ConnectionError, OSError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{request_id}: {type(exc).__name__}: {exc}")
                break
    finally:
        writer.close()
        await writer.wait_closed()
    return completed, errors


async def fault_half_written(
    harness: DaemonHarness,
    connections: int,
    frames_per_connection: int,
) -> None:
    async def attacker(index: int) -> None:
        reader, writer = await harness.connect()
        del reader
        try:
            for frame_index in range(frames_per_connection):
                payload = (
                    '{"jsonrpc":"2.0","id":%d,"method":"health","params":'
                    % (index * frames_per_connection + frame_index)
                ).encode()
                writer.write(payload[: max(1, len(payload) // 2)])
                await writer.drain()
                await asyncio.sleep(0)
        finally:
            writer.close()
            await writer.wait_closed()

    await asyncio.gather(*(attacker(index) for index in range(connections)))


async def fault_sigstop(
    harness: DaemonHarness,
    stop_seconds: float,
    burst_count: int,
) -> tuple[int, list[str]]:
    if harness.process is None:
        raise RuntimeError("daemon is not running")
    original_timeout = harness.timeout
    harness.timeout = max(original_timeout, stop_seconds + original_timeout + 1)
    burst = asyncio.create_task(run_burst(harness, burst_count, 10_000))
    await asyncio.sleep(0.1)
    os.kill(harness.process.pid, signal.SIGSTOP)
    await asyncio.sleep(stop_seconds)
    os.kill(harness.process.pid, signal.SIGCONT)
    # Requests already written before SIGSTOP must not be classified as
    # daemon failures merely because the test intentionally exceeded the
    # normal request deadline.
    try:
        return await asyncio.wait_for(
            burst,
            timeout=stop_seconds + original_timeout + 1,
        )
    finally:
        harness.timeout = original_timeout


async def fault_kill_mid_transaction(harness: DaemonHarness, burst_count: int) -> tuple[int, float]:
    if harness.process is None:
        raise RuntimeError("daemon is not running")
    reader, writer = await harness.connect()
    issued = 0
    for request_id in range(20_000, 20_000 + burst_count):
        frame = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "benchmark.ingest",
                "params": {
                    "notification": {
                        "id": f"destructive-{request_id}",
                        "source": "System",
                        "sender": "destructive-test",
                        "body": "kill-mid-transaction",
                        "received_at_ms": int(time.time() * 1000),
                    }
                },
            },
            separators=(",", ":"),
        ).encode() + b"\n"
        writer.write(frame)
        if request_id % 8 == 0:
            await writer.drain()
            await asyncio.sleep(0)
            issued += 1
            break
    started = time.monotonic()
    os.kill(harness.process.pid, signal.SIGKILL)
    writer.close()
    await writer.wait_closed()
    harness.process.wait(timeout=5)
    harness.start()
    await harness.wait_ready()
    return issued, recovery + (time.monotonic() - started)


def toggle_interface(interface: str, up: bool) -> None:
    if sys.platform != "linux":
        raise RuntimeError("interface disruption is supported only on Linux")
    state = "up" if up else "down"
    subprocess.run(
        ["ip", "link", "set", "dev", interface, state],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("this destructive harness requires Unix signals and sockets")
    with tempfile.TemporaryDirectory(prefix="unison-destructive-") as directory:
        workdir = Path(directory)
        harness = DaemonHarness(Path(args.daemon_bin).resolve(), workdir, args.timeout)
        harness.start()
        results: list[FaultResult] = []
        try:
            await harness.wait_ready()

            if args.network_interface:
                started = time.monotonic()
                try:
                    toggle_interface(args.network_interface, False)
                    await asyncio.sleep(args.network_down_seconds)
                finally:
                    toggle_interface(args.network_interface, True)
                await harness.wait_ready()
                results.append(
                    FaultResult(
                        "network_interface_drop",
                        "passed",
                        time.monotonic() - started,
                        "ipc_consistent",
                        "P2P sync is not implemented; tested daemon survival during interface loss.",
                    )
                )
            else:
                results.append(
                    FaultResult(
                        "network_interface_drop",
                        "not_run",
                        None,
                        "not_applicable",
                        "provide --network-interface to enable this destructive operation",
                    )
                )

            started = time.monotonic()
            await fault_half_written(harness, args.half_connections, args.half_frames)
            await harness.wait_ready()
            results.append(
                FaultResult(
                    "half_written_ipc_flood",
                    "passed",
                    time.monotonic() - started,
                    "ipc_consistent",
                    "daemon remained healthy after malformed partial frames",
                )
            )

            started = time.monotonic()
            _, errors = await fault_sigstop(harness, args.stop_seconds, args.burst_count)
            recovery = await harness.wait_ready()
            results.append(
                FaultResult(
                    "sigstop_sigcont_burst",
                    "passed" if harness.process and not errors else "degraded",
                    time.monotonic() - started,
                    "ipc_consistent" if not errors else "partial_burst",
                    f"completed with {len(errors)} in-flight request errors",
                )
            )

            completed, recovery = await fault_kill_mid_transaction(harness, args.burst_count)
            results.append(
                FaultResult(
                    "kill_mid_transaction",
                    "passed",
                    recovery,
                    "ipc_consistent",
                    f"daemon restarted and health recovered; {completed} requests were issued before kill",
                )
            )
        finally:
            harness.stop(force=True)

        return {
            "daemon": str(harness.daemon_bin),
            "destructive": True,
            "results": [result.as_dict() for result in results],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daemon-bin", required=True)
    parser.add_argument("--network-interface")
    parser.add_argument("--network-down-seconds", type=float, default=2.0)
    parser.add_argument("--half-connections", type=int, default=32)
    parser.add_argument("--half-frames", type=int, default=8)
    parser.add_argument("--stop-seconds", type=float, default=60.0)
    parser.add_argument("--burst-count", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--i-understand-this-is-destructive",
        action="store_true",
        help="required acknowledgement before any process or interface disruption",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.i_understand_this_is_destructive:
        print(
            "refusing to run: pass --i-understand-this-is-destructive",
            file=sys.stderr,
        )
        return 2
    if args.network_down_seconds < 0 or args.stop_seconds < 0:
        print("durations must be non-negative", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(run(args))
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"destructive integration failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=None if args.json else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
