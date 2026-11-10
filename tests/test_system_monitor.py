from collections import namedtuple

from src.engine.system_monitor import (
    PowerState,
    ResourcePolicy,
    SystemMonitor,
    TriageTier,
)


Battery = namedtuple("Battery", "percent power_plugged")


def test_battery_state_downgrades_and_flushes_once():
    flushed = []
    monitor = SystemMonitor(
        battery_probe=lambda: Battery(42.0, False),
        memory_probe=lambda: 40.0,
    )
    policy = ResourcePolicy(monitor, (lambda: flushed.append(True),))

    assert policy.refresh() is TriageTier.TIER_1
    assert policy.last_pressure is not None
    assert policy.last_pressure.power_state is PowerState.BATTERY
    assert flushed == [True]
    assert policy.refresh() is TriageTier.TIER_1
    assert flushed == [True]


def test_memory_pressure_downgrades_without_battery():
    monitor = SystemMonitor(
        battery_probe=lambda: Battery(100.0, True),
        memory_probe=lambda: 80.1,
    )

    policy = ResourcePolicy(monitor)

    assert policy.refresh() is TriageTier.TIER_1
    assert policy.last_pressure is not None
    assert policy.last_pressure.memory_pressure is True


def test_ac_and_normal_memory_use_tier_two():
    monitor = SystemMonitor(
        battery_probe=lambda: Battery(100.0, True),
        memory_probe=lambda: 79.9,
    )

    policy = ResourcePolicy(monitor)

    assert policy.refresh() is TriageTier.TIER_2
