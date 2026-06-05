"""Core lightweight diagnostics aggregator."""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import zmq
from jsonschema import ValidationError, validate

from pyxbot2_diagnostics.aggregator.config import AggregatorConfig

LOGGER = logging.getLogger(__name__)
MESSAGE_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "diagnostics_message.schema.json"


def _load_message_schema() -> dict[str, Any]:
    with MESSAGE_SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
        return json.load(schema_file)


MESSAGE_SCHEMA = _load_message_schema()


@dataclass(frozen=True, slots=True)
class DiagnosticKeyValue:
    key: str
    value: Any


@dataclass(frozen=True, slots=True)
class DiagnosticsMessage:
    v: int
    node: str
    hw_id: str
    stamp: float
    level: int
    msg: str
    values: tuple[DiagnosticKeyValue, ...]


class Sink(Protocol):
    def handle_message(self, message: DiagnosticsMessage) -> None:
        ...

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        ...

    def close(self) -> None:
        ...


class DiagnosticsAggregator:
    """Single-threaded ZMQ PULL diagnostics fan-in aggregator."""

    def __init__(
        self,
        config: AggregatorConfig,
        sinks: list[Sink] | None = None,
        *,
        context: zmq.Context | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._sinks = sinks or []
        self._time_fn = time_fn
        self._ctx = context or zmq.Context.instance()
        self._owns_ctx = context is None

        self._socket = self._ctx.socket(zmq.PULL)
        self._socket.bind(config.aggregator.zmq_endpoint)

        self.state_cache: dict[str, DiagnosticsMessage] = {}
        self._last_seen: dict[str, float] = {}

        self._running = False
        self._last_stale_check = self._time_fn()

    @staticmethod
    def validate_and_normalize_message(raw: Any) -> DiagnosticsMessage:
        """Validate and normalize raw JSON-decoded payload."""
        try:
            validate(instance=raw, schema=MESSAGE_SCHEMA)
        except ValidationError as exc:
            raise ValueError(f"Schema validation failed: {exc.message}") from exc

        normalized_values: list[DiagnosticKeyValue] = []
        for entry in raw["values"]:
            key: str
            value: Any
            if isinstance(entry, dict):
                if "key" not in entry or "value" not in entry:
                    raise ValueError("Each value object must have 'key' and 'value'")
                key = entry["key"]
                value = entry["value"]
            elif isinstance(entry, list) and len(entry) == 2:
                key = entry[0]
                value = entry[1]
            else:
                raise ValueError("Each values entry must be {'key','value'}")

            if not isinstance(key, str) or not key:
                raise ValueError("Value 'key' must be a non-empty string")
            normalized_values.append(DiagnosticKeyValue(key=key, value=value))

        return DiagnosticsMessage(
            v=1,
            node=raw["node"],
            hw_id=raw["hw_id"],
            stamp=float(raw["stamp"]),
            level=raw["level"],
            msg=raw["msg"],
            values=tuple(normalized_values),
        )

    def _mark_stale_nodes(self, now: float) -> bool:
        changed = False
        timeout = self._config.aggregator.stale_timeout_sec
        for node, last_seen in list(self._last_seen.items()):
            if (now - last_seen) <= timeout:
                continue
            current = self.state_cache[node]
            if current.level == 3:
                continue
            self.state_cache[node] = DiagnosticsMessage(
                v=current.v,
                node=current.node,
                hw_id=current.hw_id,
                stamp=now,
                level=3,
                msg="STALE",
                values=current.values,
            )
            changed = True
        return changed

    def _publish_state(self) -> None:
        snapshot = dict(self.state_cache)
        for sink in self._sinks:
            sink.publish_state(snapshot)

    def process_raw(self, raw_payload: Any, now: float | None = None) -> bool:
        """Process one raw JSON payload. Returns True if accepted."""
        try:
            message = self.validate_and_normalize_message(raw_payload)
        except ValueError as exc:
            LOGGER.warning("Rejecting malformed diagnostics message: %s", exc)
            return False

        recv_time = now if now is not None else self._time_fn()
        self.state_cache[message.node] = message
        self._last_seen[message.node] = recv_time
        for sink in self._sinks:
            sink.handle_message(message)
        self._publish_state()
        return True

    def poll_once(self, timeout_ms: int = 100) -> bool:
        """Process at most one incoming message and run stale checks."""
        processed = False
        if self._socket.poll(timeout=timeout_ms, flags=zmq.POLLIN):
            data = self._socket.recv()
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                LOGGER.warning("Rejecting non-JSON diagnostics message: %s", exc)
            else:
                processed = self.process_raw(payload)

        now = self._time_fn()
        if (now - self._last_stale_check) >= self._config.aggregator.stale_check_interval_sec:
            self._last_stale_check = now
            if self._mark_stale_nodes(now):
                self._publish_state()
        return processed

    def run(self) -> None:
        """Run aggregator loop until stop() is called or SIGINT/SIGTERM is received."""
        self._running = True

        def _stop_handler(signum: int, frame: Any) -> None:
            del signum, frame
            self.stop()

        previous_int = signal.signal(signal.SIGINT, _stop_handler)
        previous_term = signal.signal(signal.SIGTERM, _stop_handler)
        try:
            while self._running:
                self.poll_once(timeout_ms=100)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
            self.close()

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()
        self._socket.close(linger=0)
        if self._owns_ctx:
            self._ctx.term()
