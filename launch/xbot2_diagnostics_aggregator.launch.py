"""Launch the xbot2 diagnostics aggregator that publishes /diagnostics_agg."""

from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    default_config = Path(__file__).resolve().parents[1] / "config" / "aggregator.yaml"
    config_file = LaunchConfiguration("config_file")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value=str(default_config),
                description="Path to the xbot2 diagnostics aggregator configuration.",
            ),
            ExecuteProcess(
                cmd=[
                    "python3",
                    "-m",
                    "pyxbot2_diagnostics.aggregator.aggregator_node",
                    "--config",
                    config_file,
                ],
                output="screen",
            ),
        ]
    )
