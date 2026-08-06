# Fault diagnostic contract

The aggregator interprets a diagnostic message as a fault-state source only when its normalized `node` path ends in `/fault`.

Examples:

- `/xbot/joint/knee_pitch_1/fault`
- `/xbot/power/battery/fault`
- `host/robot-pc/network/eth0/fault`

Messages with other suffixes remain ordinary diagnostics even if they contain similarly named values.

## ROS string-native values

`diagnostic_msgs/KeyValue.value` is a string. A valid `/fault` message therefore contains:

- exactly one `fault_count` entry containing a non-negative whole number. The
  ROS bridge form `"1.000000"` is also accepted;
- zero or more repeated `fault_report` entries;
- `fault_count` equal to the number of unique `fault_report` values.

An empty `fault_report` value is treated as an omitted report for compatibility
with fixed-size publisher slots. It is therefore valid only together with a
zero `fault_count`.

Each `fault_report` is a standardized, stable, human-friendly identifier. It must not contain changing measurements, timestamps, counters, or other occurrence-specific text.

Active example:

```yaml
name: /xbot/joint/knee_pitch_1/fault
hardware_id: knee_pitch_1
level: 2
message: Drive faults active
values:
  - key: fault_count
    value: "2"
  - key: fault_report
    value: motor over temperature
  - key: fault_report
    value: encoder signal lost
```

Healthy example:

```yaml
name: /xbot/joint/knee_pitch_1/fault
hardware_id: knee_pitch_1
level: 0
message: OK
values:
  - key: fault_count
    value: "0"
```

The reports are the complete currently active set, not deltas. XBot2 should publish immediately when the set, severity, or summary changes and should also publish a periodic heartbeat, with 1 Hz as the default recommendation.

## Level semantics

| Reports | `level` | Meaning |
|---|---:|---|
| none | `0` | Healthy; clear all faults previously reported by this source |
| one or more | `1` | Warning-level reports active |
| one or more | `2` | Error-level reports active |
| unchanged/any | `3` | Source stale; do not raise or clear hardware faults |

Messages missing `fault_count`, containing duplicate count entries, having an inconsistent count, or having an inconsistent level/report combination are ignored for lifecycle tracking. Ignoring malformed data is safer than clearing an existing fault.

`msg` is dashboard summary text and is not part of fault identity. Dynamic measurements and vendor codes may be supplied as additional key-value entries.

The lifecycle identity is:

```text
(hw_id, node, fault_report)
```

## InfluxDB schema

Every valid fault publication, including the heartbeat, produces one `fault` point.

Tags:

- `hw_id`
- `path`
- `name`
- `component`

Fields:

- `level`
- `message`
- `fault_count`
- `active_fault_reports` (sorted reports joined for display)
- `last_fault_report`, when known
- `last_fault_active`, when known
- `last_fault_level`, when known
- `last_raised_ns`, when known
- `last_cleared_ns`, when known

This gives Grafana one current row per fault source using a latest-point query.

Each raise or clear produces one `fault_event` point.

Tags:

- `hw_id`
- `path`
- `name`
- `component`
- `fault_report`
- `transition` (`raised` or `cleared`)

Fields:

- `active`
- `level`
- `message`
- `occurrence_count`
- `first_raised_ns`
- `last_raised_ns`
- `last_cleared_ns`, when available

`fault_report` is deliberately a tag because reports are standardized and bounded, giving the same cardinality characteristics as standardized numeric fault codes while making Grafana filtering and grouping directly human-readable.

Both fault and event points use the diagnostic source timestamp when valid, falling back to aggregator wall-clock time only when necessary.

## Design rationale

The `/fault` suffix creates an explicit namespace boundary. Repeated `fault_report` entries are native to ROS string key-values and avoid JSON embedded inside strings. An explicit `fault_count` distinguishes a healthy authoritative snapshot from a publisher that omitted the contract. Complete-set publication is idempotent and lets the aggregator derive raises and clears through set differences. Periodic `fault` snapshots make the primary Grafana table robust to packet loss, subscriber startup order, and aggregator restarts, while `fault_event` points retain transition history without writing duplicate events on every heartbeat.
