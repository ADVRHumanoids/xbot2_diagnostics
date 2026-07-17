"""Host monitor orchestration and command-line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from dataclasses import replace
from typing import Any, Callable

import zmq

from pyxbot2_diagnostics.publisher import DiagPublisher

from .collectors import MetricSample, build_collectors
from .config import HostMonitorConfig, load_host_monitor_config
from .health import HealthEvaluator

LOGGER = logging.getLogger(__name__)


class HostMonitor:
    def __init__(
        self,
        config: HostMonitorConfig,
        *,
        collectors: list[Any] | None = None,
        context: zmq.Context | None = None,
        time_fn: Callable[[], float] = time.time,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._collectors = collectors if collectors is not None else build_collectors(config)
        self._context = context if context is not None else zmq.Context()
        self._owns_context = context is None
        self._time_fn = time_fn
        self._monotonic_fn = monotonic_fn
        self._sleep_fn = sleep_fn
        self._publishers: dict[str, DiagPublisher] = {}
        self._health = HealthEvaluator(
            config.thresholds.consecutive_samples,
            config.thresholds.recovery_margin,
        )
        self._collector_paths: dict[str, set[str]] = {}
        self._last_error_log: dict[str, float] = {}
        self._running = False

    def _full_path(self, suffix: str) -> str:
        return f"{self.config.node_prefix}/{self.config.hostname}/{suffix.strip('/')}"

    def _publisher(self, path: str) -> DiagPublisher:
        publisher = self._publishers.get(path)
        if publisher is None:
            publisher = DiagPublisher(
                path,
                self.config.hw_id,
                self.config.zmq_endpoint,
                self._context,
                send_hwm=1,
                immediate=True,
                time_fn=self._time_fn,
                monotonic_fn=self._monotonic_fn,
            )
            self._publishers[path] = publisher
        return publisher

    def _publish_sample(self, sample: MetricSample) -> None:
        level, message = self._health.evaluate(sample.alerts)
        if level == 0:
            message = sample.summary or f"{sample.path} metrics available"
        values = {"collector.available": 1.0, **sample.values}
        self._publisher(self._full_path(sample.path)).publish(level, message, values)

    def _handle_collector_error(self, collector: Any, exc: Exception, now: float) -> None:
        name = getattr(collector, "name", collector.__class__.__name__)
        last_log = self._last_error_log.get(name, float("-inf"))
        if now - last_log >= 30.0:
            LOGGER.warning("Host collector %s failed: %s", name, exc)
            self._last_error_log[name] = now
        for path in self._collector_paths.get(name, set()):
            self._publisher(path).publish(
                1,
                f"WARN: {name} collection failed ({type(exc).__name__})",
                {"collector.available": 0.0},
            )

    def sample_once(self) -> None:
        monotonic_now = self._monotonic_fn()
        for collector in self._collectors:
            name = getattr(collector, "name", collector.__class__.__name__)
            try:
                samples = collector.sample(monotonic_now)
            except Exception as exc:  # collector isolation is intentional
                self._handle_collector_error(collector, exc, monotonic_now)
                continue
            paths: set[str] = set()
            for sample in samples:
                self._publish_sample(sample)
                paths.add(self._full_path(sample.path))
            if paths:
                self._collector_paths[name] = paths

    def run(self) -> None:
        self._running = True
        deadline = self._monotonic_fn()
        while self._running:
            self.sample_once()
            deadline += self.config.sample_interval_sec
            delay = deadline - self._monotonic_fn()
            if delay > 0:
                self._sleep_fn(delay)
            else:
                deadline = self._monotonic_fn()

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        for publisher in self._publishers.values():
            publisher.close()
        self._publishers.clear()
        if self._owns_context:
            self._context.term()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish local host diagnostics over ZeroMQ")
    parser.add_argument("--config", help="Path to a YAML configuration file")
    parser.add_argument("--endpoint", help="Override the ZeroMQ aggregator endpoint")
    parser.add_argument("--once", action="store_true", help="Collect and publish one sample, then exit")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_host_monitor_config(args.config)
    if args.endpoint is not None:
        config = replace(config, zmq_endpoint=args.endpoint)

    monitor = HostMonitor(config)
    previous_handlers: dict[int, Any] = {}

    def stop_handler(signum: int, frame: Any) -> None:
        del signum, frame
        monitor.stop()

    try:
        if args.once:
            monitor.sample_once()
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[sig] = signal.signal(sig, stop_handler)
            monitor.run()
    except KeyboardInterrupt:
        monitor.stop()
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        monitor.close()
    return 0
