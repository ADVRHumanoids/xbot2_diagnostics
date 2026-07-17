[![CI](https://github.com/ADVRHumanoids/xbot2_diagnostics/actions/workflows/ci.yml/badge.svg)](https://github.com/ADVRHumanoids/xbot2_diagnostics/actions/workflows/ci.yml)
# xbot2_diagnostics
Diagnostics and observability for HHCM's xbot2 framework.

## Lightweight diagnostics aggregator

A Python aggregator is available under `/aggregator`.

### Features
- ZMQ `PULL` fan-in on configurable endpoint (`tcp://localhost:9268` default)
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
  zmq_endpoint: "tcp://localhost:9268"
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

## Local host monitor

`xbot2-host-monitor` collects Linux CPU, memory, temperature, filesystem, disk I/O,
network, uptime, and optional battery/NVIDIA GPU metrics. It publishes independent
`host/<hostname>/...` diagnostics to the same ZMQ aggregator without requiring ROS.
The Python implementation supports Python 3.8 and newer.
By default, temperature telemetry contains only the minimum, average, and maximum
across recognized CPU sensors; individual, disk, and other temperatures are omitted.
When `/proc/xenomai/sched/stat` is available, the monitor also publishes each
non-IRQ Xenomai scheduler entry with its mode-switch count and CPU load.

```bash
xbot2-host-monitor --config config/host_monitor.yaml
# A single best-effort sample for smoke testing:
xbot2-host-monitor --config config/host_monitor.yaml --once
```

The endpoint precedence is `--endpoint`, `host_monitor.zmq_endpoint`,
`XBOT_DIAG_ENDPOINT`, then `tcp://localhost:9268`. The config file itself can be
selected with `--config` or `XBOT2_HOST_MONITOR_CONFIG`. Metric thresholds,
collectors, interface filters, and required interfaces are documented in
`config/host_monitor.yaml`.

For boot-time operation, install the project and enable the supplied unit:

```bash
sudo systemctl enable --now xbot2-host-monitor.service
```

Site-specific `XBOT2_HOST_MONITOR_CONFIG` or `XBOT_DIAG_ENDPOINT` values can be
placed in `/etc/default/xbot2-host-monitor`.


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
