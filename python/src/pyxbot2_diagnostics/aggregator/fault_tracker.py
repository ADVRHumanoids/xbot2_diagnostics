"""Fault lifecycle tracking for normalized health diagnostics messages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


# Fault lifecycle contract:
# - only diagnostic paths ending in /health are considered;
# - fault_codes is the canonical required value key;
# - an empty fault_codes collection means that all previous faults are cleared;
# - levels 1/2 describe active faults, level 0 describes no active faults;
# - level 3 is transport staleness and must not change hardware fault state.
_CANONICAL_CODE_KEY = "fault_codes"
_LEGACY_SINGLE_CODE_KEYS = {"fault_code", "error_code"}
_LEGACY_MULTI_CODE_KEYS = {
    "error_codes",
    "active_fault_codes",
    "active_error_codes",
}


@dataclass(frozen=True)
class FaultKey:
    """Stable identity for one device fault."""

    hw_id: str
    node: str
    code: str


@dataclass(frozen=True)
class FaultState:
    """Current lifecycle state for one device fault."""

    key: FaultKey
    active: bool
    level: int
    message: str
    first_raised: float
    last_raised: float
    last_cleared: float | None
    occurrence_count: int


@dataclass(frozen=True)
class FaultTransition:
    """A fault state transition emitted by :class:`FaultLifecycleTracker`."""

    kind: str
    state: FaultState
    stamp: float


class FaultLifecycleTracker:
    """Track coded faults published through the health-message contract.

    Canonical publishers use a node path ending in ``/health`` and include one
    ``fault_codes`` value. Its value is the complete set of faults active at the
    message timestamp. An empty collection clears faults previously reported by
    that health source.

    ``fault_code``, ``error_code``, ``error_codes``, ``active_fault_codes`` and
    ``active_error_codes`` are accepted as compatibility aliases, but new
    publishers should only emit ``fault_codes``.
    """

    def __init__(self) -> None:
        self.states: dict[FaultKey, FaultState] = {}
        self._active_by_source: dict[tuple[str, str], set[str]] = {}

    def update(self, message: Any, recv_time: float) -> list[FaultTransition]:
        """Consume one normalized message and return fault transitions."""
        if not self._is_health_node(message.node) or message.level == 3:
            return []

        codes, has_code_key = self._extract_codes(message.values)
        if not has_code_key:
            # A /health message without the required code set is malformed for
            # lifecycle purposes. Ignoring it is safer than clearing state.
            return []

        source = (message.hw_id or "unknown", message.node)
        previous_codes = set(self._active_by_source.get(source, set()))
        current_codes = codes

        # fault_codes is the source of truth. Level conveys severity only.
        # Inconsistent messages are ignored to avoid false raises or clears.
        if current_codes and message.level not in (1, 2):
            return []
        if not current_codes and message.level != 0:
            return []

        stamp = self._event_stamp(message.stamp, recv_time)
        transitions: list[FaultTransition] = []

        for code in sorted(previous_codes - current_codes):
            key = FaultKey(source[0], source[1], code)
            previous = self.states[key]
            state = FaultState(
                key=key,
                active=False,
                level=0,
                message=message.msg,
                first_raised=previous.first_raised,
                last_raised=previous.last_raised,
                last_cleared=stamp,
                occurrence_count=previous.occurrence_count,
            )
            self.states[key] = state
            transitions.append(FaultTransition("cleared", state, stamp))

        for code in sorted(current_codes - previous_codes):
            key = FaultKey(source[0], source[1], code)
            previous = self.states.get(key)
            state = FaultState(
                key=key,
                active=True,
                level=message.level,
                message=message.msg,
                first_raised=stamp if previous is None else previous.first_raised,
                last_raised=stamp,
                last_cleared=None if previous is None else previous.last_cleared,
                occurrence_count=1 if previous is None else previous.occurrence_count + 1,
            )
            self.states[key] = state
            transitions.append(FaultTransition("raised", state, stamp))

        for code in sorted(current_codes & previous_codes):
            key = FaultKey(source[0], source[1], code)
            previous = self.states[key]
            self.states[key] = FaultState(
                key=key,
                active=True,
                level=message.level,
                message=message.msg,
                first_raised=previous.first_raised,
                last_raised=previous.last_raised,
                last_cleared=previous.last_cleared,
                occurrence_count=previous.occurrence_count,
            )

        self._active_by_source[source] = current_codes
        return transitions

    @staticmethod
    def _is_health_node(node: Any) -> bool:
        parts = [part for part in str(node).split("/") if part]
        return bool(parts) and parts[-1].lower() == "health"

    @staticmethod
    def _event_stamp(source_stamp: Any, recv_time: float) -> float:
        try:
            stamp = float(source_stamp)
        except (TypeError, ValueError):
            return recv_time
        return stamp if math.isfinite(stamp) and stamp > 0.0 else recv_time

    @classmethod
    def _extract_codes(cls, values: Iterable[Any]) -> tuple[set[str], bool]:
        codes: set[str] = set()
        has_code_key = False

        for item in values:
            key = str(item.key).strip().lower()
            value = item.value
            if key == _CANONICAL_CODE_KEY or key in _LEGACY_MULTI_CODE_KEYS:
                has_code_key = True
                for raw_code in cls._iter_codes(value):
                    code = cls._normalize_code(raw_code)
                    if code is not None:
                        codes.add(code)
            elif key in _LEGACY_SINGLE_CODE_KEYS:
                has_code_key = True
                code = cls._normalize_code(value)
                if code is not None:
                    codes.add(code)

        return codes, has_code_key

    @staticmethod
    def _iter_codes(value: Any) -> Iterable[Any]:
        if isinstance(value, (list, tuple, set)):
            return value
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value]

    @staticmethod
    def _normalize_code(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return None if value == 0 else f"0x{value:X}"
        if isinstance(value, float) and value.is_integer():
            integer = int(value)
            return None if integer == 0 else f"0x{integer:X}"

        text = str(value).strip()
        if not text or text.lower() in {"0", "0x0", "0x0000", "none", "ok"}:
            return None
        if text.lower().startswith("0x"):
            try:
                return f"0x{int(text, 16):X}"
            except ValueError:
                pass
        return text
