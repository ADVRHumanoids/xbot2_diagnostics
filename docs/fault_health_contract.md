# Fault health diagnostic contract

The aggregator interprets a diagnostic message as a fault-state source only when its normalized `node` path ends in `/health`.

Examples of health-source paths:

- `/xbot/joint/knee_pitch_1/health`
- `/xbot/power/battery/health`
- `host/robot-pc/network/eth0/health`

Messages with other suffixes remain ordinary diagnostics and are not inspected for fault lifecycle information, even if they contain similarly named values.

## Required values

Every `/health` message must contain exactly one canonical key-value entry named `fault_codes`.

```json
{"key": "fault_codes", "value": ["0x4210", "0x7500"]}
```

`fault_codes` is the complete set of fault codes active for that health source at the message timestamp. It is not a delta and must not contain only newly raised faults.

The value should be an array. Each code may be an integer or a stable string identifier. Integer codes and hexadecimal strings are normalized to uppercase hexadecimal strings by the aggregator. Zero-like values (`0`, `0x0000`, `none`, `ok`) are ignored and must not be used as real fault identifiers.

An empty array explicitly means that the source has no active faults:

```json
{"key": "fault_codes", "value": []}
```

A `/health` message without `fault_codes` is invalid for lifecycle tracking and is ignored. It does not clear previously active faults.

## Level and message semantics

The ROS diagnostics level must agree with `fault_codes`:

| `fault_codes` | `level` | Meaning |
|---|---:|---|
| empty | `0` | Healthy; clear all faults previously reported by this source |
| non-empty | `1` | One or more warning-level faults are active |
| non-empty | `2` | One or more error-level faults are active |
| unchanged/any | `3` | Source is stale; do not raise or clear hardware faults |

Inconsistent combinations, such as non-empty `fault_codes` with level `0`, are ignored for lifecycle tracking.

`msg` is a human-readable summary for dashboards and logs. It is not part of fault identity and must not be parsed to determine active faults.

`hw_id` identifies the physical device. A fault lifecycle is keyed by:

```text
(hw_id, node, fault_code)
```

## Examples

Active faults:

```json
{
  "v": 1,
  "node": "/xbot/joint/knee_pitch_1/health",
  "hw_id": "knee_pitch_1",
  "stamp": 1785967012.0,
  "level": 2,
  "msg": "Drive reports over-temperature and communication faults",
  "values": [
    {"key": "fault_codes", "value": ["0x4210", "0x7500"]}
  ]
}
```

Healthy/cleared:

```json
{
  "v": 1,
  "node": "/xbot/joint/knee_pitch_1/health",
  "hw_id": "knee_pitch_1",
  "stamp": 1785967305.0,
  "level": 0,
  "msg": "OK",
  "values": [
    {"key": "fault_codes", "value": []}
  ]
}
```

## Compatibility aliases

For migration, the tracker currently accepts these aliases:

- single-code aliases: `fault_code`, `error_code`
- multi-code aliases: `error_codes`, `active_fault_codes`, `active_error_codes`

New publishers must use `fault_codes`. Compatibility aliases may be removed in a future schema version.

## Design rationale

The path suffix provides an explicit namespace boundary so arbitrary telemetry cannot accidentally create or clear faults. A complete active-code set makes updates idempotent and allows the aggregator to compute raises and clears by set difference. It also supports devices that report one current code and devices that report multiple simultaneous codes without changing the storage model.
