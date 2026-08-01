# Device health diagnostic schema (draft v1)

This document describes the provisional schema recognized by the Python diagnostics
aggregator. It is intentionally narrow while the device-health contract is being
reviewed.

## Identification

A diagnostic is treated as a device-health message when the final segment of its
`DiagnosticStatus.name` / aggregator `node` is either:

- `health`
- `health_status`

The preceding path identifies the logical device. `hardware_id` must contain the
non-empty physical device identifier.

Example:

```text
name:        /xbot/joint/left_knee/motor/health_status
device path: /xbot/joint/left_knee/motor
hardware_id: SN-0028417
```

## Required key-value fields

All required values may arrive as JSON strings, as they do in ROS
`diagnostic_msgs/KeyValue`. Native JSON values are also accepted by the ZMQ input.

| Key | Type | Meaning |
|---|---|---|
| `schema.name` | string | Must equal `xbot.device_health` |
| `schema.version` | integer or integer string | Must equal `1` |
| `device.boot_id` | non-empty string | Counter epoch identifier |
| `faults.active` | array of unique strings | Complete currently active fault set |
| `faults.raise_count_total` | object: fault code to non-negative integer | Monotonic raise count within `device.boot_id` |
| `faults.last_raised` | object: fault code to timestamp or null | Latest raise time |
| `faults.last_cleared` | object: fault code to timestamp or null | Latest clear time |

Timestamps may be non-negative Unix epoch seconds or timezone-aware ISO-8601
strings. They are normalized to integer nanoseconds.

Optional non-contract key-value fields are retained on the `device_health` InfluxDB
point. Scalar values remain scalar fields; structured values are serialized as
compact JSON strings.

## Consistency rules

- Diagnostic keys must be unique.
- Every active or timestamped fault code must exist in
  `faults.raise_count_total`.
- A positive raise counter requires a non-null last-raise time.
- A zero raise counter requires a null or absent last-raise time.
- An active fault must have a positive raise counter.
- For an active fault, `last_cleared` must precede `last_raised` when both exist.
- For an inactive fault, `last_cleared` must not precede `last_raised` when both
  exist.

Malformed health messages are logged and omitted from InfluxDB health measurements.
Other diagnostic messages continue through the generic InfluxDB path unchanged.

## InfluxDB measurements

### `device_health`

One point is written per valid health snapshot.

Tags:

- `hw_id`
- `path`
- `device_path`
- `schema`
- `schema_version`
- `boot_id`

Fields:

- `level`
- `active_fault_count`
- `message`, when non-empty
- optional non-contract health values

The point timestamp is the diagnostic source timestamp.

### `fault_counter`

One point is written per known fault code in each valid health snapshot.

Tags:

- `hw_id`
- `path`
- `device_path`
- `fault_code`
- `schema_version`
- `boot_id`

Fields:

- `active`
- `raise_count_total`
- `last_raised_ns`, when known
- `last_cleared_ns`, when known

The point timestamp is the diagnostic source timestamp.

## Decisions still open

1. Whether the canonical suffix should be only `/health_status`, only `/health`,
   or whether both aliases should remain supported.
2. Whether `device.boot_id` is mandatory and whether counters are boot-scoped or
   persisted for the lifetime of the device.
3. Whether `boot_id` should be an InfluxDB tag. Keeping it as a tag simplifies
   counter-epoch filtering but creates a new series for every device reboot.
4. Whether per-fault timestamps should stay as integer nanosecond fields or be
   represented differently for easier Grafana formatting.
5. Whether optional device-specific health values should share the
   `device_health` measurement or be written through the existing generic
   diagnostic measurement.
