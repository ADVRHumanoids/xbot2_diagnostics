import logging

import pytest

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage
from pyxbot2_diagnostics.aggregator.health import (
    HealthMessageValidationError,
    is_health_message,
    parse_health_message,
)
from pyxbot2_diagnostics.aggregator.sinks.influxdb_sink import InfluxDBSink


class FakeWriteApi:
    def __init__(self) -> None:
        self.calls = []

    def write(self, *, bucket, org, record):
        self.calls.append({"bucket": bucket, "org": org, "record": record})


def _health_msg(
    *,
    node: str = "/xbot/joint/knee/motor/health_status",
    hw_id: str = "SN-1",
    stamp: float = 1785614401.25,
    values: tuple[DiagnosticKeyValue, ...] | None = None,
) -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id=hw_id,
        stamp=stamp,
        level=2,
        msg="OVERCURRENT active",
        values=values
        or (
            DiagnosticKeyValue("schema.name", "xbot.device_health"),
            DiagnosticKeyValue("schema.version", "1"),
            DiagnosticKeyValue("device.boot_id", "boot-a"),
            DiagnosticKeyValue("faults.active", '["OVERCURRENT"]'),
            DiagnosticKeyValue(
                "faults.raise_count_total",
                '{"OVERCURRENT":4,"ENCODER_CRC":2}',
            ),
            DiagnosticKeyValue(
                "faults.last_raised",
                '{"OVERCURRENT":"2026-08-01T20:00:00Z",'
                '"ENCODER_CRC":"2026-08-01T19:00:00+00:00"}',
            ),
            DiagnosticKeyValue(
                "faults.last_cleared",
                '{"OVERCURRENT":"2026-08-01T18:00:00Z",'
                '"ENCODER_CRC":"2026-08-01T19:00:01Z"}',
            ),
            DiagnosticKeyValue("thermal.temperature", 72.5),
            DiagnosticKeyValue("communication.degraded", False),
        ),
    )


def _replace_value(
    message: DiagnosticsMessage,
    key: str,
    value,
) -> DiagnosticsMessage:
    values = tuple(
        DiagnosticKeyValue(entry.key, value if entry.key == key else entry.value)
        for entry in message.values
    )
    return DiagnosticsMessage(
        v=message.v,
        node=message.node,
        hw_id=message.hw_id,
        stamp=message.stamp,
        level=message.level,
        msg=message.msg,
        values=values,
    )


def test_parse_valid_health_message() -> None:
    health = parse_health_message(_health_msg())

    assert health.device_path == "/xbot/joint/knee/motor"
    assert health.boot_id == "boot-a"
    assert health.active_fault_count == 1
    assert [fault.code for fault in health.faults] == ["ENCODER_CRC", "OVERCURRENT"]
    assert health.faults[0].active is False
    assert health.faults[1].active is True
    assert [entry.key for entry in health.extra_values] == [
        "thermal.temperature",
        "communication.degraded",
    ]


def test_accepts_native_json_values_and_health_alias() -> None:
    message = _health_msg(node="xbot/motor/health")
    message = _replace_value(message, "faults.active", ["OVERCURRENT"])
    message = _replace_value(
        message,
        "faults.raise_count_total",
        {"OVERCURRENT": 4, "ENCODER_CRC": 2},
    )
    message = _replace_value(
        message,
        "faults.last_raised",
        {"OVERCURRENT": 1785614400.0, "ENCODER_CRC": 1785610800.0},
    )
    message = _replace_value(
        message,
        "faults.last_cleared",
        {"OVERCURRENT": 1785607200.0, "ENCODER_CRC": 1785610801.0},
    )

    assert is_health_message(message)
    assert parse_health_message(message).device_path == "/xbot/motor"


@pytest.mark.parametrize(
    "key,value,match",
    [
        ("faults.active", '["OVERCURRENT","OVERCURRENT"]', "duplicates"),
        ("faults.raise_count_total", '{"OVERCURRENT":-1}', "non-negative"),
        (
            "faults.last_raised",
            '{"OVERCURRENT":"2026-08-01T20:00:00"}',
            "timezone",
        ),
    ],
)
def test_rejects_invalid_fault_payloads(key, value, match) -> None:
    with pytest.raises(HealthMessageValidationError, match=match):
        parse_health_message(_replace_value(_health_msg(), key, value))


def test_rejects_duplicate_diagnostic_keys() -> None:
    original = _health_msg()
    message = _health_msg(
        values=original.values + (DiagnosticKeyValue("faults.active", "[]"),)
    )
    with pytest.raises(HealthMessageValidationError, match="duplicate keys"):
        parse_health_message(message)


def test_rejects_active_fault_missing_from_counter_map() -> None:
    message = _replace_value(
        _health_msg(), "faults.raise_count_total", '{"ENCODER_CRC":2}'
    )
    with pytest.raises(
        HealthMessageValidationError, match="missing referenced fault codes"
    ):
        parse_health_message(message)


def test_rejects_positive_counter_without_last_raise() -> None:
    message = _replace_value(
        _health_msg(),
        "faults.last_raised",
        '{"OVERCURRENT":null,"ENCODER_CRC":"2026-08-01T19:00:00Z"}',
    )
    with pytest.raises(HealthMessageValidationError, match="positive raise counter"):
        parse_health_message(message)


def test_requires_health_suffix_and_device_path() -> None:
    assert not is_health_message(_health_msg(node="/xbot/motor/temperature"))
    with pytest.raises(HealthMessageValidationError, match="must end"):
        parse_health_message(_health_msg(node="/xbot/motor/temperature"))
    with pytest.raises(HealthMessageValidationError, match="device path"):
        parse_health_message(_health_msg(node="/health"))


def test_influx_sink_normalizes_health_message() -> None:
    fake = FakeWriteApi()
    sink = InfluxDBSink(
        enabled=True,
        url="",
        token="",
        org="xbot2",
        bucket="diagnostics",
        write_api=fake,
    )

    sink.handle_message(_health_msg())
    sink._last_flush = 0.0
    sink.publish_state({})

    points = fake.calls[0]["record"]
    assert [point["measurement"] for point in points] == [
        "device_health",
        "fault_counter",
        "fault_counter",
    ]

    health_point = points[0]
    assert health_point["tags"]["hw_id"] == "SN-1"
    assert health_point["tags"]["device_path"] == "/xbot/joint/knee/motor"
    assert health_point["tags"]["boot_id"] == "boot-a"
    assert health_point["fields"]["level"] == 2
    assert health_point["fields"]["active_fault_count"] == 1
    assert health_point["fields"]["thermal.temperature"] == 72.5
    assert health_point["fields"]["communication.degraded"] is False
    assert health_point["time"] == 1785614401250000000

    fault_points = {point["tags"]["fault_code"]: point for point in points[1:]}
    assert fault_points["OVERCURRENT"]["fields"]["active"] is True
    assert fault_points["OVERCURRENT"]["fields"]["raise_count_total"] == 4
    assert fault_points["ENCODER_CRC"]["fields"]["active"] is False
    assert fault_points["ENCODER_CRC"]["fields"]["last_cleared_ns"] > 0


def test_influx_sink_omits_invalid_health_message(caplog) -> None:
    fake = FakeWriteApi()
    sink = InfluxDBSink(
        enabled=True,
        url="",
        token="",
        org="xbot2",
        bucket="diagnostics",
        write_api=fake,
    )
    invalid = _replace_value(_health_msg(), "schema.version", "99")

    with caplog.at_level(logging.WARNING):
        sink.handle_message(invalid)
    sink._last_flush = 0.0
    sink.publish_state({})

    assert fake.calls == []
    assert "unsupported health schema version" in caplog.text
