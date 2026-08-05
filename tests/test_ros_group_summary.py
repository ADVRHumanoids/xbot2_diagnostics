from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage
from pyxbot2_diagnostics.aggregator.sinks.ros_diagnostics_sink import RosDiagnosticsSink


def _msg(node: str, level: int, message: str) -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id="hw",
        stamp=1.0,
        level=level,
        msg=message,
        values=(),
    )


def _level_value(level):
    return level[0] if isinstance(level, bytes) else level


def test_group_message_summarizes_immediate_child_levels() -> None:
    published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=published.append,
        time_fn=lambda: 1.0,
    )

    sink.publish_state(
        {
            "/xbot/group/warn_a/metric": _msg("/xbot/group/warn_a/metric", 1, "warn"),
            "/xbot/group/warn_b/metric": _msg("/xbot/group/warn_b/metric", 1, "warn"),
            "/xbot/group/error/metric": _msg("/xbot/group/error/metric", 2, "error"),
            "/xbot/group/stale_a/metric": _msg("/xbot/group/stale_a/metric", 3, "stale"),
            "/xbot/group/stale_b/metric": _msg("/xbot/group/stale_b/metric", 3, "stale"),
            "/xbot/group/stale_c/metric": _msg("/xbot/group/stale_c/metric", 3, "stale"),
            "/xbot/group/stale_d/metric": _msg("/xbot/group/stale_d/metric", 3, "stale"),
            "/xbot/group/ok/metric": _msg("/xbot/group/ok/metric", 0, "OK"),
        }
    )

    statuses = {status.name: status for status in published[0].status}
    group = statuses["/Robot/xbot/group"]
    assert _level_value(group.level) == 3
    assert group.message == "2 WARN, 1 ERROR, 4 STALE"


def test_group_summary_counts_direct_children_not_descendant_leaves() -> None:
    published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=published.append,
        time_fn=lambda: 1.0,
    )

    sink.publish_state(
        {
            "/xbot/arm/joint_a/temperature": _msg(
                "/xbot/arm/joint_a/temperature", 1, "warn"
            ),
            "/xbot/arm/joint_a/voltage": _msg(
                "/xbot/arm/joint_a/voltage", 2, "error"
            ),
            "/xbot/arm/joint_b/temperature": _msg(
                "/xbot/arm/joint_b/temperature", 0, "OK"
            ),
        }
    )

    statuses = {status.name: status for status in published[0].status}
    assert statuses["/Robot/xbot/arm"].message == "1 ERROR"
    assert statuses["/Robot/xbot/arm/joint_a"].message == "1 WARN, 1 ERROR"
    assert statuses["/Robot/xbot/arm/joint_b"].message == "OK"


def test_leaf_message_is_preserved() -> None:
    published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=published.append,
        time_fn=lambda: 1.0,
    )
    sink.publish_state(
        {"/xbot/drive/health": _msg("/xbot/drive/health", 2, "Drive fault")}
    )

    statuses = {status.name: status for status in published[0].status}
    assert statuses["/Robot/xbot/drive/health"].message == "Drive fault"
