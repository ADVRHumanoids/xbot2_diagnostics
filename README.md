[![CI](https://github.com/ADVRHumanoids/xbot2_diagnostics/actions/workflows/ci.yml/badge.svg)](https://github.com/ADVRHumanoids/xbot2_diagnostics/actions/workflows/ci.yml)
# xbot2_diagnostics
Diagnostics and observability for HHCM's xbot2 framework.

## Lightweight diagnostics aggregator

A Python aggregator is available under `/aggregator`.

### Features
- ZMQ `PULL` fan-in on configurable endpoint (`tcp://localhost:5555` default)
- Per-node state cache
- Stale detection (`level=3`) with configurable timeout/check interval
- JSON schema validation for incoming diagnostics messages
- Pluggable sinks:
  - InfluxDB v2 export (optional)
  - ROS `/diagnostics` publisher (optional)
  - JSONL file logger (optional, rolling)
  - Stdout pretty printer (optional)

### Message schema (`v=1`)
```json
{
  "v": 1,
  "node": "controller/joint_impedance",
  "hw_id": "arm_left",
  "stamp": 1717500000.123,
  "level": 0,
  "msg": "OK",
  "values": [
    {"key": "torque_error.mean", "value": 0.0031},
    {"key": "torque_error.std", "value": 0.0008}
  ]
}
```

### Configuration
Pass with `--config` or `XBOT2_DIAGNOSTICS_CONFIG`.

```yaml
aggregator:
  zmq_endpoint: "tcp://localhost:5555"
  stale_timeout_sec: 5.0
  stale_check_interval_sec: 1.0

sinks:
  influxdb:
    enabled: false
    url: "http://localhost:8086"
    token: "${INFLUXDB_TOKEN}"
    org: "xbot2"
    bucket: "diagnostics"

  ros_diagnostics:
    enabled: false
    publish_rate_hz: 1.0

  json_file:
    enabled: false
    path: "/tmp/diagnostics.jsonl"
    max_file_size_mb: 100

  stdout:
    enabled: false
    interval_sec: 10.0
```

### Run
```bash
python -m aggregator.aggregator_node --config /path/to/config.yaml
```
