# Device health diagnostic contract

A diagnostic is treated as a device-health message when the final segment of its
`DiagnosticStatus.name` / aggregator `node` is either `health` or `health_status`.
The preceding path identifies the logical device. `hardware_id` must contain the
non-empty physical device identifier.

Example:

```text
name:        /xbot/joint/left_knee/motor/health_status
device path: /xbot/joint/left_knee/motor
hardware_id: SN-0028417
```

## Required key-value fields

Values may arrive as JSON strings, as they do in ROS
`diagnostic_msgs/KeyValue`. Native JSON values are also accepted by the ZMQ input.

| Key | Type | Meaning |
|---|---|---|
| `device.boot_id` | non-empty string | Counter epoch identifier |
| `faults.active` | array of unique strings | Complete currently active fault set |
| `faults.raise_count_total` | object: fault code to non-negative integer | Monotonic raise count within `device.boot_id` |
| `faults.last_raised` | object: fault code to timestamp or null | Latest raise time |
| `faults.last_cleared` | object: fault code to timestamp or null | Latest clear time |

Timestamps may be non-negative Unix epoch seconds or timezone-aware ISO-8601
strings. Internally they are normalized to nanoseconds. InfluxDB fields exposed to
Grafana use Unix epoch milliseconds (`last_raised_ms`, `last_cleared_ms`).

Optional device-specific key-value fields are retained on the `device_health`
InfluxDB point. Scalar values remain scalar fields; structured values are serialized
as compact JSON strings.

## Validation rules

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
- `device_path`

Fields:

- `level`
- `active_fault_count`
- `boot_id`
- `message`, when non-empty
- optional device-specific health values

The point timestamp is the diagnostic source timestamp.

### `fault_counter`

One point is written per known fault code in each valid health snapshot.

Tags:

- `hw_id`
- `device_path`
- `fault_code`

Fields:

- `active`
- `raise_count_total`
- `boot_id`
- `last_raised_ms`, when known
- `last_cleared_ms`, when known

The point timestamp is the diagnostic source timestamp.

### `fault_occurrence`

A sparse point is written when a cumulative raise counter increases.

Tags:

- `hw_id`
- `device_path`
- `fault_code`

Fields:

- `occurrences`: counter delta since the previous received sample
- `counter_before`
- `counter_after`
- `boot_id`
- `last_raised_ms`, when known

The point timestamp is the current health diagnostic source timestamp. When the
counter delta is greater than one, the exact timestamps of all raises are not known;
`last_raised_ms` records the latest raise supplied by the producer.

The first sample for a fault establishes a baseline. A changed `boot_id` also
establishes a new baseline. A counter decrease within the same boot is logged as a
warning and establishes a new baseline. None of these baseline cases emits a
`fault_occurrence` point.

`boot_id` is stored as an Influx field, not a tag, to avoid creating a new series on
every reboot.

## Open decisions

1. Whether the canonical suffix should be only `/health_status`, only `/health`,
   or whether both aliases should remain supported.
2. Whether `device.boot_id` remains mandatory and whether counters are boot-scoped
   or persisted for the lifetime of the device.
3. Whether optional device-specific health values should share the
   `device_health` measurement or use the generic diagnostic measurement.
