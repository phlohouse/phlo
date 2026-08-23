"""Freshness check plugin for validating data timeliness.

This module provides the FreshnessCheckPlugin, which enables validation of
data freshness based on timestamp columns. It helps ensure that data is
being updated within acceptable timeframes, critical for time-sensitive
analytics and operational dashboards.

Example:
    Using the freshness check plugin::

        from datetime import datetime, timedelta
        from phlo_core.quality.freshness_check import FreshnessCheckPlugin

        # Create the plugin
        plugin = FreshnessCheckPlugin()

        # Check that data is no more than 24 hours old
        check = plugin.create_check(
            timestamp_column="updated_at",
            max_age_hours=24.0
        )

        # Check against a specific reference time
        reference = datetime.now() - timedelta(hours=12)
        check_with_ref = plugin.create_check(
            timestamp_column="created_at",
            max_age_hours=6.0,
            reference_time=reference
        )

"""

from datetime import datetime
from typing import Any

from phlo.plugins import PluginMetadata, QualityCheckPlugin


class FreshnessCheckPlugin(QualityCheckPlugin[Any]):
    """Create freshness checks that validate whether data is within an acceptable
    age based on a timestamp column.

    Each check compares the newest timestamp value in the data against a reference
    time (current time by default) and fails when the data is older than the
    configured threshold. Useful for monitoring pipeline latency, ensuring
    dashboards show current data, detecting stale sources, and validating ETL job
    success.

    Example:
        Basic freshness check with current time as reference::

            from phlo_core.quality.freshness_check import FreshnessCheckPlugin

            plugin = FreshnessCheckPlugin()
            check = plugin.create_check(
                timestamp_column="event_time",
                max_age_hours=2.0
            )

        Freshness check with custom reference time::

            from datetime import datetime, timedelta

            yesterday = datetime.now() - timedelta(days=1)
            check = plugin.create_check(
                timestamp_column="ingested_at",
                max_age_hours=1.0,
                reference_time=yesterday
            )

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the freshness-check plugin."""
        return PluginMetadata(
            name="freshness_check",
            version="0.1.0",
            description="Freshness checks for timestamped data",
            author="Phlo Team",
            tags=["quality", "freshness"],
        )

    def create_check(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Create and return a configured FreshnessCheck from phlo_pandera that
        validates data freshness based on timestamps.

        The check fails when the newest ``timestamp_column`` value is older than
        ``max_age_hours`` relative to ``reference_time`` (current time when omitted).
        The returned object can be used with Pandera schemas or called directly with
        DataFrames. Raises: TypeError when arguments are missing or of the wrong
        type, or when unknown arguments are supplied.

        Example:
            Create a freshness check for recent data::

                from phlo_core.quality.freshness_check import FreshnessCheckPlugin

                plugin = FreshnessCheckPlugin()
                check = plugin.create_check(
                    timestamp_column="created_at",
                    max_age_hours=24.0
                )

            Create a freshness check with custom reference::

                from datetime import datetime, timedelta

                check_time = datetime.now() - timedelta(hours=12)
                check = plugin.create_check(
                    timestamp_column="updated_at",
                    max_age_hours=6.0,
                    reference_time=check_time
                )

        """
        if len(args) > 3 or set(kwargs) - {"timestamp_column", "max_age_hours", "reference_time"}:
            raise TypeError("create_check accepts timestamp_column, max_age_hours, reference_time")
        timestamp_column = kwargs.get("timestamp_column", args[0] if args else None)
        max_age_hours = kwargs.get("max_age_hours", args[1] if len(args) > 1 else None)
        reference_time = kwargs.get("reference_time", args[2] if len(args) > 2 else None)
        if not isinstance(timestamp_column, str) or not isinstance(max_age_hours, (int, float)):
            raise TypeError("timestamp_column and max_age_hours are required")
        if reference_time is not None and not isinstance(reference_time, datetime):
            raise TypeError("reference_time must be a datetime or None")

        from phlo_pandera.checks import FreshnessCheck

        return FreshnessCheck(
            timestamp_column=timestamp_column,
            max_age_hours=max_age_hours,
            reference_time=reference_time,
        )
