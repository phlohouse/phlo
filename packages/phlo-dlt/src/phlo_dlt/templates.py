"""DLT-owned project templates shipped with phlo.

Each template renders on top of MinimalTemplate and reuses the core template
writers so generated projects share one layout; templates() enumerates the set.
"""

from __future__ import annotations

from phlo.cli.templates.builtin import (
    MinimalTemplate,
    _write_project_readme,
    _write_pyproject_toml,
    _write_text,
)
from phlo.cli.templates.models import ProjectTemplate, TemplateMetadata, TemplateRenderContext


class CsvBatchTemplate:
    metadata = TemplateMetadata(
        name="csv-batch",
        description="Local CSV batch pipeline",
        required_packages=("phlo", "phlo-dlt", "phlo-pandera"),
        generated_paths=(
            "data/events.csv",
            "workflows/ingestion/csv/events.py",
            "workflows/schemas/csv.py",
        ),
        next_steps=("phlo test", "phlo materialize dlt_events --partition 2025-01-15"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Render the CSV batch ingestion template into the project directory."""
        MinimalTemplate().render(context)
        _write_project_readme(
            context.project_dir,
            context.project_name,
            template_commands=("phlo materialize dlt_events --partition 2025-01-15",),
        )
        _write_pyproject_toml(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
        _write_text(
            context.project_dir / "data" / "events.csv", "id,name,value\n1,alpha,10\n2,beta,20\n"
        )
        _write_text(
            context.project_dir / "workflows" / "schemas" / "csv.py",
            """from __future__ import annotations

import pandera.pandas as pa


class EventsSchema(pa.DataFrameModel):
    '''CSV demo event records.'''

    event_id: str
    id: int
    name: str
    value: int
""",
        )
        _write_text(
            context.project_dir / "workflows" / "ingestion" / "csv" / "events.py",
            """from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo

from workflows.schemas.csv import EventsSchema


@phlo.ingestion(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="csv",
    freshness_hours=(1, 24),
)
def csv_events(partition_date: str) -> object:
    events = pd.read_csv(Path("data/events.csv"))
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="events")
""",
        )


class ApiIngestionTemplate:
    metadata = TemplateMetadata(
        name="api-ingestion",
        description="REST API ingestion pipeline",
        required_packages=("phlo", "phlo-dlt", "phlo-pandera"),
        generated_paths=("workflows/ingestion/api/events.py", "workflows/schemas/api.py"),
        next_steps=("phlo test", "phlo materialize dlt_events --partition 2025-01-15"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Render the REST API ingestion template into the project directory."""
        MinimalTemplate().render(context)
        _write_project_readme(
            context.project_dir,
            context.project_name,
            template_commands=("phlo materialize dlt_events --partition 2025-01-15",),
        )
        _write_pyproject_toml(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
        _write_text(
            context.project_dir / "workflows" / "schemas" / "api.py",
            """from __future__ import annotations

import pandera.pandas as pa


class EventsSchema(pa.DataFrameModel):
    '''API demo event records.'''

    event_id: str
    id: int
    name: str
""",
        )
        _write_text(
            context.project_dir / "workflows" / "ingestion" / "api" / "events.py",
            """from __future__ import annotations

import pandas as pd
import phlo
import dlt

from workflows.schemas.api import EventsSchema


@phlo.ingestion(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="api",
    freshness_hours=(1, 24),
)
def api_events(partition_date: str) -> object:
    events = pd.DataFrame([{"id": 1, "name": "sample"}])
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="events")
""",
        )


class ObservabilityDemoTemplate:
    metadata = TemplateMetadata(
        name="observability-demo",
        description="Pipeline with telemetry wiring",
        required_packages=("phlo", "phlo-dlt", "phlo-pandera", "phlo-otel"),
        generated_paths=("workflows/ingestion/observability/events.py",),
        next_steps=("phlo services init", "phlo services start --profile observability"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Render the observability demo template into the project directory."""
        CsvBatchTemplate().render(context)
        _write_pyproject_toml(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
        _write_text(
            context.project_dir / "workflows" / "ingestion" / "observability" / "events.py",
            """from __future__ import annotations

import logging

import dlt
import pandas as pd
import phlo

from workflows.schemas.csv import EventsSchema

logger = logging.getLogger(__name__)


@phlo.ingestion(
    table_name="observability_events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="observability",
    freshness_hours=(1, 24),
)
def observability_events(partition_date: str) -> object:
    logger.info("loading observability demo events")
    events = pd.DataFrame([{"id": 1, "name": "traceable", "value": 1}])
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="observability_events")
""",
        )


def templates() -> tuple[ProjectTemplate, ...]:
    """Return every built-in project template in canonical order."""
    return (CsvBatchTemplate(), ApiIngestionTemplate(), ObservabilityDemoTemplate())
