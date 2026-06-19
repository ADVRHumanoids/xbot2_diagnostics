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
  - ROS `/diagnostics` subscriber and `/diagnostics_agg` publisher (optional)
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
    input_topic: "/diagnostics"
    aggregated_topic: "/diagnostics_agg"
    publish_aggregated: true
    aggregation_root: "Robot"

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
python -m pyxbot2_diagnostics.aggregator.aggregator_node --config /path/to/config.yaml
```


## Local Grafana and InfluxDB

A local Docker Compose stack is provided for visualizing aggregated ROS diagnostics with Grafana.
Docker runs only InfluxDB and Grafana; ROS nodes run on the host so they can join the ROS graph normally.
The host ROS environment must provide the `diagnostic_remote_logging` package.

1. Create a local environment file:

```bash
cp docker/.env.example docker/.env
```

2. Start InfluxDB and Grafana:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.diagnostics.yml up -d
```

3. Start the diagnostics aggregator, which subscribes to `/diagnostics` and publishes `/diagnostics_agg`:

```bash
ros2 launch launch/xbot2_diagnostics_aggregator.launch.py
```

4. Start `diagnostic_remote_logging`, remapped so it writes `/diagnostics_agg` to InfluxDB:

```bash
source docker/.env
ros2 launch launch/diagnostic_remote_logging.launch.py
```

Grafana is available at `http://localhost:3000` and InfluxDB at `http://localhost:8086`.
The default development credentials are documented in `docker/.env.example`; use local secrets for anything beyond development.

