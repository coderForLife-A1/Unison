"""Local power and resource monitoring for adaptive triage."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import psutil

logger = logging.getLogger(__name__)

MEMORY_PRESSURE_THRESHOLD = 80.0


class PowerState(str, Enum):
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"


class TriageTier(str, Enum):
    TIER_1 = "tier-1"
    TIER_2 = "tier-2"


@dataclass(frozen=True)
class SystemPressure:
    power_state: PowerState
    battery_percent: float | None
    battery_saver: bool
    memory_percent: float
    memory_pressure: bool

    @property
    def constrained(self) -> bool:
        return self.battery_saver or self.memory_pressure


class SystemMonitor:
    """Read OS power and memory state with injectable probes for tests.

    ``psutil.sensors_battery`` exposes the portable AC/battery state on the
    supported platforms. On battery, Unison conservatively treats the host as
    battery-saver constrained because platform-specific saver APIs are not
    consistent across Windows, Linux, and macOS.
    """

    def __init__(
        self,
        *,
        battery_probe: Callable[[], Any | None] | None = None,
        memory_probe: Callable[[], float] | None = None,
    ) -> None:
        self._battery_probe = battery_probe or psutil.sensors_battery
        self._memory_probe = memory_probe or (lambda: psutil.virtual_memory().percent)

    def read(self) -> SystemPressure:
        battery = self._battery_probe()
        memory_percent = float(self._memory_probe())
        if not 0 <= memory_percent <= 100:
            raise ValueError("memory pressure probe must return a percentage from 0 to 100")

        if battery is None or battery.power_plugged is None:
            power_state = PowerState.UNKNOWN
            battery_percent = None if battery is None else float(battery.percent)
        elif battery.power_plugged:
            power_state = PowerState.AC
            battery_percent = float(battery.percent)
        else:
            power_state = PowerState.BATTERY
            battery_percent = float(battery.percent)

        # An explicit override is useful for managed deployments and tests.
        forced_saver = os.getenv("UNISON_BATTERY_SAVER", "").lower()
        battery_saver = (
            forced_saver in {"1", "true", "yes", "on"}
            or power_state is PowerState.BATTERY
        )
        return SystemPressure(
            power_state=power_state,
            battery_percent=battery_percent,
            battery_saver=battery_saver,
            memory_percent=memory_percent,
            memory_pressure=memory_percent > MEMORY_PRESSURE_THRESHOLD,
        )


class ResourcePolicy:
    """Apply system pressure to triage tier and non-essential cache hooks."""

    def __init__(
        self,
        monitor: SystemMonitor | None = None,
        cache_flushers: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self.monitor = monitor or SystemMonitor()
        self._cache_flushers = cache_flushers
        self._tier = TriageTier.TIER_2
        self._last_pressure: SystemPressure | None = None

    @property
    def tier(self) -> TriageTier:
        return self._tier

    @property
    def last_pressure(self) -> SystemPressure | None:
        return self._last_pressure

    def refresh(self) -> TriageTier:
        pressure = self.monitor.read()
        next_tier = TriageTier.TIER_1 if pressure.constrained else TriageTier.TIER_2
        if next_tier is TriageTier.TIER_1 and self._tier is TriageTier.TIER_2:
            for flush in self._cache_flushers:
                try:
                    flush()
                except Exception:
                    logger.warning("Failed to flush a non-essential cache", exc_info=True)
        if next_tier is not self._tier:
            logger.info(
                "Triage tier changed from %s to %s (power=%s memory=%.1f%%)",
                self._tier.value,
                next_tier.value,
                pressure.power_state.value,
                pressure.memory_percent,
            )
        self._tier = next_tier
        self._last_pressure = pressure
        return self._tier

    def add_cache_flusher(self, flusher: Callable[[], None]) -> None:
        """Register a cache to release on the next constrained transition."""
        self._cache_flushers = (*self._cache_flushers, flusher)
