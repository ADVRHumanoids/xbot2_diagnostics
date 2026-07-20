"""Launch diagnostic_remote_logging for the local InfluxDB stack."""

from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = Path(__file__).resolve().parents[1] / "config" / "diagnostic_remote_logging.yaml"
    config_file = LaunchConfiguration("config_file")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value=str(default_config),
                description="Path to the diagnostic_remote_logging parameter file.",
            ),
            Node(
                package="diagnostic_remote_logging",
                executable="influx",
                name="influxdb_connector",
                parameters=[config_file],
                output="screen",
            ),
        ]
    )
