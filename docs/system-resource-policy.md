# System resource policy

Unison monitors local power and memory state through
[`src/engine/system_monitor.py`](../src/engine/system_monitor.py).

## Policy

- AC power and memory usage at or below 80%: **Tier-2** local SLM and
  embedding/vector deduplication are enabled.
- Battery power (treated conservatively as battery-saver mode) or memory usage
  above 80%: **Tier-1** deterministic rules only.
- Entering Tier-1 releases the in-memory FastEmbed model and transient vector
  table handle once. Persistent LanceDB data is not deleted.
- Tier-1 classifies OTP/security codes and urgent/deadline/critical messages as
  high priority, promotions as noise, and other messages as low priority.
- When the next poll observes normal conditions, Tier-2 is restored
  automatically.

`SystemMonitor` accepts injectable probes so power and memory transitions can be
tested without changing host state. `UNISON_BATTERY_SAVER=1` can force the
constrained policy for local diagnostics.
