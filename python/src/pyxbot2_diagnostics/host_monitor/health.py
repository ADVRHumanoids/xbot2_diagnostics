"""Stateful threshold evaluation with debounce and hysteresis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertObservation:
    key: str
    label: str
    value: float
    warn: float
    error: float | None
    direction: str = "high"
    unit: str = ""


@dataclass(slots=True)
class _AlertState:
    level: int = 0
    candidate: int = 0
    count: int = 0


class HealthEvaluator:
    def __init__(self, consecutive_samples: int = 3, recovery_margin: float = 5.0) -> None:
        self._consecutive = consecutive_samples
        self._margin = recovery_margin
        self._states: dict[str, _AlertState] = {}

    def _target(self, observation: AlertObservation, current: int) -> int:
        value = observation.value
        error = observation.error
        if observation.direction == "high":
            if current == 2 and error is not None and value >= error - self._margin:
                return 2
            if error is not None and value >= error:
                return 2
            if current >= 1 and value >= observation.warn - self._margin:
                return 1
            return 1 if value >= observation.warn else 0
        if observation.direction != "low":
            raise ValueError(f"Unsupported alert direction: {observation.direction}")
        if current == 2 and error is not None and value <= error + self._margin:
            return 2
        if error is not None and value <= error:
            return 2
        if current >= 1 and value <= observation.warn + self._margin:
            return 1
        return 1 if value <= observation.warn else 0

    def evaluate(self, observations: list[AlertObservation]) -> tuple[int, str]:
        active: list[tuple[int, str]] = []
        for observation in observations:
            state = self._states.setdefault(observation.key, _AlertState())
            target = self._target(observation, state.level)
            if target == state.level:
                state.candidate = target
                state.count = 0
            else:
                if state.candidate == target:
                    state.count += 1
                else:
                    state.candidate = target
                    state.count = 1
                if state.count >= self._consecutive:
                    state.level = target
                    state.count = 0
            if state.level:
                threshold = observation.error if state.level == 2 else observation.warn
                operator = ">=" if observation.direction == "high" else "<="
                active.append((
                    state.level,
                    f"{observation.label} {observation.value:.1f}{observation.unit} "
                    f"{operator} {threshold:.1f}{observation.unit}",
                ))

        if not active:
            return 0, "OK"
        level = max(item[0] for item in active)
        prefix = "ERROR" if level == 2 else "WARN"
        messages = [message for item_level, message in active if item_level == level]
        return level, f"{prefix}: " + "; ".join(messages[:3])
