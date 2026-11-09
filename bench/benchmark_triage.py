#!/usr/bin/env python3
"""Benchmark Unison notification triage with a reproducible synthetic corpus.

The default ``rules`` engine is deterministic and requires no model server.
Use ``--engine ollama`` to benchmark the repository's local Instructor/Ollama
engine. Resource measurements require psutil:

    python -m pip install psutil
    python bench/benchmark_triage.py --engine rules
    python bench/benchmark_triage.py --engine ollama --model qwen2.5:3b

The benchmark intentionally reports both critical-alert misses and high-priority
false positives. A "critical alert false-positive rate" alone is ambiguous:
critical messages can be missed (false negatives), while non-critical messages
can be incorrectly escalated (false positives).
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

try:
    import psutil
except ImportError as exc:  # pragma: no cover - exercised by CLI users
    raise SystemExit(
        "psutil is required for CPU/RAM measurements. "
        "Install it with: python -m pip install psutil"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class SyntheticNotification:
    """Notification plus independently generated expected labels."""

    notification_id: str
    source: str
    sender: str
    content: str
    expected_category: str
    expected_priority: str
    expected_action_required: bool
    critical: bool


@dataclass(frozen=True)
class Prediction:
    category: str
    priority: str
    action_required: bool


class Classifier(Protocol):
    async def classify(self, notification: SyntheticNotification) -> Prediction:
        """Return a structured prediction for one notification."""


def _notification(
    index: int,
    source: str,
    sender: str,
    content: str,
    category: str,
    priority: str,
    action_required: bool,
    critical: bool = False,
) -> SyntheticNotification:
    return SyntheticNotification(
        notification_id=f"synthetic-{index:04d}",
        source=source,
        sender=sender,
        content=content,
        expected_category=category,
        expected_priority=priority,
        expected_action_required=action_required,
        critical=critical,
    )


def generate_dataset(seed: int = 7, count: int = 200) -> list[SyntheticNotification]:
    """Generate exactly 200 balanced, diverse notifications.

    Five families contribute 40 examples each. The seed changes wording
    permutations without changing the expected-label distribution.
    """

    if count != 200:
        raise ValueError("The benchmark corpus is defined as exactly 200 notifications")

    import random

    rng = random.Random(seed)
    rows: list[SyntheticNotification] = []
    templates = [
        ("otp", "SMS", "SecureBank", "Your verification code is {code}. It expires in 10 minutes.", "OTP", "High", True, True),
        ("otp", "Email", "GitHub Security", "GitHub login verification code: {code}. Do not share this code.", "OTP", "High", True, True),
        ("otp", "WhatsApp", "Acme Auth", "Use {code} as your one-time password to sign in.", "OTP", "High", True, True),
        ("otp", "SMS", "Parcel Delivery", "Your delivery confirmation code is {code}.", "OTP", "High", True, True),
        ("slack", "System", "Slack · #platform", "{name} mentioned you: can you review the production rollout before 16:00?", "Work", "High", True, True),
        ("slack", "System", "Slack · #engineering", "Build {build} failed on main; please investigate the failing deployment.", "Work", "High", True, True),
        ("slack", "System", "Slack · #general", "{name} shared an engineering update for the team.", "Work", "Low", False, False),
        ("slack", "System", "Slack · #random", "New thread reply: lunch plans for Friday.", "Social", "Low", False, False),
        ("marketing", "Email", "Trail Outfitters", "{pct}% off summer gear. Shop the sale today with code SUN.", "Promo", "Noise", False, False),
        ("marketing", "Email", "Daily Recipes", "This week's newsletter: five quick vegetarian dinners.", "Promo", "Noise", False, False),
        ("marketing", "Email", "Cloud Vendor", "Upgrade now and receive bonus credits for a limited time.", "Promo", "Noise", False, False),
        ("marketing", "Email", "Travel Deals", "Flash sale: discounted flights end tonight.", "Promo", "Noise", False, False),
        ("calendar", "System", "Calendar", "Reminder: {meeting} starts in 15 minutes at {room}.", "Calendar", "High", True, True),
        ("calendar", "System", "Calendar", "Invitation: {meeting} scheduled for tomorrow at 09:00.", "Calendar", "Low", True, False),
        ("calendar", "System", "Calendar", "Event updated: {meeting} moved to next Tuesday.", "Calendar", "Low", True, False),
        ("calendar", "System", "Calendar", "Your focus block begins in 30 minutes.", "Calendar", "Low", False, False),
        ("github", "System", "GitHub", "Critical alert: repository secret may have been exposed; rotate it immediately.", "Security", "High", True, True),
        ("github", "System", "GitHub", "Pull request #{pr} is ready for your review.", "Work", "High", True, False),
        ("github", "System", "GitHub Actions", "Workflow {workflow} completed successfully on branch main.", "Work", "Low", False, False),
        ("github", "System", "Dependabot", "Dependency update available for package {package}.", "Work", "Low", False, False),
    ]
    index = 0
    for family, source, sender, template, category, priority, action, critical in templates:
        for _ in range(10):
            index += 1
            values = {
                "code": f"{rng.randrange(100000, 999999)}",
                "name": rng.choice(["Priya", "Mateo", "Lin", "Jordan"]),
                "build": rng.choice(["#1842", "#1843", "#1844"]),
                "pct": rng.choice(["15", "20", "30", "40"]),
                "meeting": rng.choice(["Platform stand-up", "incident review", "customer demo"]),
                "room": rng.choice(["Room 2", "Zoom", "HQ-301"]),
                "pr": str(rng.randrange(120, 999)),
                "workflow": rng.choice(["test", "release", "lint"]),
                "package": rng.choice(["fastapi", "react", "sqlite-utils"]),
            }
            rows.append(_notification(
                index,
                source,
                sender,
                template.format(**values),
                category,
                priority,
                action,
                critical,
            ))
    rng.shuffle(rows)
    return rows


class RuleClassifier:
    """Fast deterministic baseline mirroring Stage 1 triage behavior."""

    OTP = re.compile(r"\b(?:otp|one[- ]time password|verification code|security code)\b|\b\d{6}\b", re.I)
    CRITICAL = re.compile(r"\b(?:critical|immediately|production|failed|exposed|rotate)\b", re.I)
    CALENDAR = re.compile(r"\b(?:calendar|meeting|event|starts in|scheduled|focus block)\b", re.I)
    GITHUB = re.compile(r"\b(?:pull request|workflow|dependabot|repository|github actions)\b", re.I)
    PROMO = re.compile(r"\b(?:sale|discount|off|newsletter|upgrade|bonus credits|deals|coupon)\b", re.I)
    ACTION = re.compile(r"\b(?:review|reply|verify|rotate|investigate|starts in|scheduled|invitation)\b|\?", re.I)

    async def classify(self, notification: SyntheticNotification) -> Prediction:
        text = f"{notification.sender} {notification.content}"
        if self.OTP.search(text):
            return Prediction("OTP", "High", True)
        if self.PROMO.search(text):
            return Prediction("Promo", "Noise", False)
        if self.CALENDAR.search(text):
            urgent = bool(re.search(r"\b(?:starts in|15 minutes)\b", text, re.I))
            return Prediction("Calendar", "High" if urgent else "Low", True if "focus block" not in text else False)
        if self.GITHUB.search(text):
            critical = bool(re.search(r"\b(?:critical|exposed|rotate)\b", text, re.I))
            return Prediction("Security" if critical else "Work", "High" if critical or "review" in text.lower() else "Low", critical or "review" in text.lower())
        if "slack" in notification.sender.lower() or "mentioned you" in text.lower() or "build" in text.lower():
            critical = bool(self.CRITICAL.search(text))
            return Prediction("Work" if critical or "#engineering" in text else "Social", "High" if critical else "Low", critical)
        return Prediction("General", "Low", bool(self.ACTION.search(text)))


class OllamaClassifier:
    """Adapter around the repository's existing local Instructor/Ollama engine."""

    def __init__(self, model: str | None = None) -> None:
        from src.engine.llm_client import OllamaClient
        from src.engine.unison import UnisonEngine

        self._engine = UnisonEngine(OllamaClient(model=model))

    async def classify(self, notification: SyntheticNotification) -> Prediction:
        from src.models.schemas import IngestedNotification, NotificationBatch

        # Existing schema intentionally has a narrow source Literal. Preserve
        # the synthetic application in sender while using System as its channel.
        source = notification.source if notification.source in {"SMS", "Email", "WhatsApp", "System"} else "System"
        item = IngestedNotification(
            id=notification.notification_id,
            source=source,
            sender=notification.sender,
            content=notification.content,
            timestamp=datetime.now(timezone.utc),
        )
        summary = await self._engine.unison_batch(NotificationBatch(
            notifications=[item],
            representative_content=notification.content,
        ))
        return Prediction(summary.category, summary.priority, summary.action_required)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    lower, upper = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def score(dataset: list[SyntheticNotification], predictions: list[Prediction]) -> dict[str, float | int]:
    correct = sum(
        prediction.category == item.expected_category
        and prediction.priority == item.expected_priority
        and prediction.action_required == item.expected_action_required
        for item, prediction in zip(dataset, predictions)
    )
    critical = [i for i, item in enumerate(dataset) if item.critical]
    noncritical = [i for i, item in enumerate(dataset) if not item.critical]
    critical_misses = sum(predictions[i].priority != "High" for i in critical)
    high_false_positives = sum(predictions[i].priority == "High" for i in noncritical)
    return {
        "correct": correct,
        "accuracy_percent": 100.0 * correct / len(dataset),
        "critical_count": len(critical),
        "critical_miss_rate_percent": 100.0 * critical_misses / len(critical),
        "noncritical_high_false_positive_rate_percent": 100.0 * high_false_positives / len(noncritical),
    }


async def run(
    classifier: Classifier,
    dataset: list[SyntheticNotification],
    burst_rate: float,
) -> tuple[list[Prediction], list[float], dict[str, float]]:
    process = psutil.Process(os.getpid())
    rss_before = process.memory_info().rss
    cpu_before = process.cpu_times()
    predictions: list[Prediction] = []
    latencies_ms: list[float] = []
    interval = 1.0 / burst_rate
    next_submission = time.perf_counter()
    peak_rss = rss_before
    started = time.perf_counter()

    for notification in dataset:
        now = time.perf_counter()
        if now < next_submission:
            await asyncio.sleep(next_submission - now)
        request_start = time.perf_counter()
        predictions.append(await classifier.classify(notification))
        latencies_ms.append((time.perf_counter() - request_start) * 1000)
        peak_rss = max(peak_rss, process.memory_info().rss)
        next_submission += interval

    elapsed = time.perf_counter() - started
    cpu_after = process.cpu_times()
    cpu_seconds = (cpu_after.user - cpu_before.user) + (cpu_after.system - cpu_before.system)
    return predictions, latencies_ms, {
        "elapsed_seconds": elapsed,
        "throughput_per_second": len(dataset) / elapsed if elapsed else 0.0,
        "rss_before_mb": rss_before / 1024**2,
        "rss_peak_mb": peak_rss / 1024**2,
        "rss_delta_mb": (peak_rss - rss_before) / 1024**2,
        "cpu_seconds": cpu_seconds,
        "cpu_percent_of_one_core": 100.0 * cpu_seconds / elapsed if elapsed else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("rules", "ollama"), default="rules")
    parser.add_argument("--model", default=None, help="Local Ollama model tag for --engine ollama")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--burst-rate", type=float, default=50.0, help="Synthetic arrival rate in notifications/sec")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    dataset = generate_dataset(seed=args.seed)
    classifier: Classifier = RuleClassifier() if args.engine == "rules" else OllamaClassifier(args.model)
    gc.collect()
    predictions, latencies, resources = await run(classifier, dataset, args.burst_rate)
    result = {
        "engine": args.engine,
        "model": args.model,
        "seed": args.seed,
        "notification_count": len(dataset),
        "burst_rate_target_per_second": args.burst_rate,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p99": percentile(latencies, 99),
            "min": min(latencies),
            "max": max(latencies),
        },
        "classification": score(dataset, predictions),
        "resources": resources,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Engine: {args.engine}; notifications: {len(dataset)}; target burst: {args.burst_rate:g}/sec")
        print(f"Accuracy: {result['classification']['accuracy_percent']:.2f}%")
        print(f"Latency: P50={result['latency_ms']['p50']:.2f} ms, P99={result['latency_ms']['p99']:.2f} ms")
        print(f"Critical miss rate: {result['classification']['critical_miss_rate_percent']:.2f}%")
        print("Non-critical high-priority false-positive rate: "
              f"{result['classification']['noncritical_high_false_positive_rate_percent']:.2f}%")
        print(f"Throughput: {resources['throughput_per_second']:.2f}/sec")
        print(f"CPU: {resources['cpu_percent_of_one_core']:.2f}% of one core")
        print(f"RAM: {resources['rss_before_mb']:.2f} -> {resources['rss_peak_mb']:.2f} MB "
              f"(+{resources['rss_delta_mb']:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
