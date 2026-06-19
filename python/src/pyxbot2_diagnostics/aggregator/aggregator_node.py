"""Entry point for diagnostics aggregator with optional ROS diagnostics I/O."""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsAggregator, ZmqDiagnosticsSource
from pyxbot2_diagnostics.aggregator.config import CONFIG_ENV_VAR, AggregatorConfig, load_config
from pyxbot2_diagnostics.aggregator.sinks import InfluxDBSink, JsonFileSink, RosDiagnosticsSink, StdoutSink
from pyxbot2_diagnostics.aggregator.sources import RosDiagnosticsSource

LOGGER = logging.getLogger(__name__)


def _build_ros_io(config: AggregatorConfig) -> tuple[RosDiagnosticsSource | None, RosDiagnosticsSink | None]:
    ros_config = config.sinks.ros_diagnostics
    if not ros_config.enabled:
        return None, None

    try:
        import rclpy
        from diagnostic_msgs.msg import DiagnosticArray
        from rclpy.node import Node
    except ImportError:
        LOGGER.warning("rclpy/diagnostic_msgs not available: ROS diagnostics disabled")
        return None, None

    rclpy.init(args=None)
    node = Node("xbot2_diagnostics_aggregator")

    source = RosDiagnosticsSource(
        node=node,
        diagnostic_array_type=DiagnosticArray,
        input_topic=ros_config.input_topic,
        spin_once=rclpy.spin_once,
        time_fn=time.time,
        shutdown=rclpy.shutdown,
    )

    sink = None
    if ros_config.publish_aggregated:
        publisher = node.create_publisher(DiagnosticArray, ros_config.aggregated_topic, 10)

        def _publish(msg: Any) -> None:
            publisher.publish(msg)

        sink = RosDiagnosticsSink(
            aggregated_publisher=_publish,
            aggregation_root=ros_config.aggregation_root,
            time_fn=time.time,
            stamp_fn=lambda: node.get_clock().now().to_msg(),
        )

    return source, sink


def build_sinks(config: AggregatorConfig, ros_sink: RosDiagnosticsSink | None = None) -> list[Any]:
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

    if ros_sink is not None:
        sinks.append(ros_sink)

    return sinks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run xbot2 diagnostics aggregator")
    parser.add_argument("--config", default=None, help=f"YAML config path (or ${CONFIG_ENV_VAR})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    config = load_config(args.config)
    ros_source, ros_sink = _build_ros_io(config)
    sources = [ZmqDiagnosticsSource(config.aggregator.zmq_endpoint)]
    if ros_source is not None:
        sources.append(ros_source)
    aggregator = DiagnosticsAggregator(
        config=config,
        sinks=build_sinks(config, ros_sink),
        sources=sources,
    )
    LOGGER.info("Starting diagnostics aggregator on %s", config.aggregator.zmq_endpoint)
    aggregator.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
