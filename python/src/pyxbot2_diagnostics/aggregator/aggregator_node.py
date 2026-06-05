"""Entry point for diagnostics aggregator with optional ROS output sink."""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsAggregator
from pyxbot2_diagnostics.aggregator.config import CONFIG_ENV_VAR, AggregatorConfig, load_config
from pyxbot2_diagnostics.aggregator.sinks import InfluxDBSink, JsonFileSink, RosDiagnosticsSink, StdoutSink

LOGGER = logging.getLogger(__name__)


def _build_ros_sink(config: AggregatorConfig) -> RosDiagnosticsSink | None:
    if not config.sinks.ros_diagnostics.enabled:
        return None

    try:
        import rclpy
        from diagnostic_msgs.msg import DiagnosticArray
        from rclpy.node import Node
    except ImportError:
        LOGGER.warning("rclpy/diagnostic_msgs not available: ROS sink disabled")
        return None

    rclpy.init(args=None)
    node = Node("xbot2_diagnostics_aggregator")
    publisher = node.create_publisher(DiagnosticArray, "/diagnostics", 10)

    def _publish(msg: Any) -> None:
        publisher.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.0)

    return RosDiagnosticsSink(
        publish_rate_hz=config.sinks.ros_diagnostics.publish_rate_hz,
        publisher=_publish,
        time_fn=time.time,
        stamp_fn=lambda: node.get_clock().now().to_msg(),
    )


def build_sinks(config: AggregatorConfig) -> list[Any]:
    sinks: list[Any] = []

    sinks.append(
        InfluxDBSink(
            enabled=config.sinks.influxdb.enabled,
            url=config.sinks.influxdb.url,
            token=config.sinks.influxdb.token,
            org=config.sinks.influxdb.org,
            bucket=config.sinks.influxdb.bucket,
        )
    )

    if config.sinks.json_file.enabled:
        sinks.append(
            JsonFileSink(
                path=config.sinks.json_file.path,
                max_file_size_mb=config.sinks.json_file.max_file_size_mb,
            )
        )

    if config.sinks.stdout.enabled:
        sinks.append(StdoutSink(interval_sec=config.sinks.stdout.interval_sec))

    ros_sink = _build_ros_sink(config)
    if ros_sink is not None:
        sinks.append(ros_sink)

    return sinks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run xbot2 diagnostics aggregator")
    parser.add_argument("--config", default=None, help=f"YAML config path (or ${CONFIG_ENV_VAR})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    config = load_config(args.config)
    aggregator = DiagnosticsAggregator(config=config, sinks=build_sinks(config))
    LOGGER.info("Starting diagnostics aggregator on %s", config.aggregator.zmq_endpoint)
    aggregator.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
