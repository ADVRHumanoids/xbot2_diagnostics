"""JSON file sink for diagnostics messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage


class JsonFileSink:
    """Append incoming messages to JSON-lines file with simple rolling."""

    def __init__(self, path: str, max_file_size_mb: float) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = int(max_file_size_mb * 1024 * 1024)

    def _roll_if_needed(self) -> None:
        if not self._path.exists() or self._path.stat().st_size < self._max_bytes:
            return
        backup = self._path.with_suffix(self._path.suffix + ".1")
        if backup.exists():
            backup.unlink()
        self._path.rename(backup)

    def handle_message(self, message: DiagnosticsMessage) -> None:
        self._roll_if_needed()
        data: dict[str, Any] = {
            "v": message.v,
            "node": message.node,
            "hw_id": message.hw_id,
            "stamp": message.stamp,
            "level": message.level,
            "msg": message.msg,
            "values": [{"key": kv.key, "value": kv.value} for kv in message.values],
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, separators=(",", ":")) + "\n")

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        del states

    def close(self) -> None:
        return
