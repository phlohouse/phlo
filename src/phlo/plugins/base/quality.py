"""Quality check plugin classes.

This module defines plugin types for custom data quality validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from phlo.plugins.base.plugin import Plugin

TQualityCheck = TypeVar("TQualityCheck")


class QualityCheckPlugin(Plugin, ABC, Generic[TQualityCheck]):
    """Base class for quality check plugins.

    Quality check plugins enable custom data validation logic
    beyond the built-in checks.

    Example:
        ```python
        from phlo_quality.checks import QualityCheck, QualityCheckResult

        class BusinessRuleCheck(QualityCheckPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="business_rule",
                    version="1.0.0",
                    description="Validate business rules",
                )

            def create_check(self, **kwargs) -> QualityCheck:
                rule = kwargs.get("rule")
                return BusinessRuleQualityCheck(rule=rule)


        class BusinessRuleQualityCheck(QualityCheck):
            def __init__(self, rule: str):
                self.rule = rule

            def execute(self, df: pd.DataFrame, context: Any) -> QualityCheckResult:
                # Implement rule validation
                violations = df.query(f"not ({self.rule})")

                return QualityCheckResult(
                    passed=len(violations) == 0,
                    metric_name="business_rule",
                    metric_value={"violations": len(violations)},
                )

            @property
            def name(self) -> str:
                return f"business_rule_{self.rule}"
        ```

    """

    @abstractmethod
    def create_check(self, *args: Any, **kwargs: Any) -> TQualityCheck:
        """Create a quality check instance.

        This factory method creates instances of quality checks
        that can be used with @phlo_quality decorator.

        Example:
            ```python
            def create_check(self, column: str, threshold: float) -> QualityCheck:
                return CustomQualityCheck(column=column, threshold=threshold)
            ```
        """
