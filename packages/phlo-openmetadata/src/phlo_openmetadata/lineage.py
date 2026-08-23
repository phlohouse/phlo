"""Lineage extraction and publishing for OpenMetadata.

Extracts lineage information from Dagster and dbt,
and publishes it to OpenMetadata for data discovery and impact analysis.

Example:
    >>> from phlo_openmetadata.lineage import LineageExtractor
    >>> extractor = LineageExtractor()
    >>> extractor.extract_from_dbt_manifest(manifest_dict)
    >>> stats = extractor.publish_to_openmetadata(client)

"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from phlo.logging import get_logger

logger = get_logger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


def log_extraction_errors(source_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that logs exceptions with source context and re-raises them.

    source_name identifies the lineage source (e.g., 'Dagster', 'dbt') in
    the log record.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        """Wrap an extraction function with source-aware error logging."""

        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Execute the wrapped callable, logging then re-raising any failure."""
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                logger.error(
                    "lineage_extraction_failed",
                    source=source_name,
                    error=str(exc),
                )
                raise

        return wrapper

    return decorator


class LineageExtractor:
    """Extracts lineage from Dagster, dbt, and Iceberg into a unified graph.

    Example:
        >>> extractor = LineageExtractor()
        >>> extractor.extract_from_dbt_manifest(manifest)
        >>> print(extractor.export_lineage("mermaid"))

    """

    def __init__(self):
        """Initialize the extractor with an empty lineage graph."""
        from phlo_openmetadata.graph import OpenMetadataLineageGraph

        self.graph = OpenMetadataLineageGraph()

    @log_extraction_errors("Dagster")
    def extract_from_dagster(self, context: Any) -> None:
        """Extract lineage from a Dagster execution context."""
        if not hasattr(context, "get_asset_materialization_events"):
            logger.warning(
                "Unsupported Dagster execution context for lineage extraction. "
                "Expected an ExecuteInProcessResult-like object with "
                "get_asset_materialization_events()."
            )
            return

        events = context.get_asset_materialization_events()
        if not isinstance(events, list):
            logger.warning(
                "dagster_materialization_events_invalid_type",
                events_type=type(events).__name__,
            )
            return

        for event in events:
            asset_key = getattr(event, "asset_key", None)
            if asset_key is None:
                continue
            if hasattr(asset_key, "path") and asset_key.path:
                asset_name = asset_key.path[-1]
            else:
                asset_name = str(asset_key)
            self.graph.add_asset(asset_name, asset_type="unknown")

        logger.info(
            "dagster_lineage_assets_extracted",
            asset_count=len(self.graph.assets),
        )

    @log_extraction_errors("dbt")
    def extract_from_dbt_manifest(self, manifest: dict[str, Any]) -> None:
        """Extract assets and model dependencies from a parsed dbt manifest."""
        for unique_id, node in manifest.get("nodes", {}).items():
            if unique_id.startswith("model."):
                model_name = node.get("name")
                if not model_name:
                    logger.warning("dbt_model_name_missing", unique_id=unique_id)
                    continue
                self.graph.add_asset(
                    model_name,
                    asset_type="transform",
                    status="unknown",
                )

        for unique_id, source in manifest.get("sources", {}).items():
            src_name_part = source.get("source_name") or ""
            tbl_name_part = source.get("name") or ""
            source_name = f"{src_name_part}.{tbl_name_part}".strip(".")
            if not source_name:
                logger.warning("dbt_source_name_missing", unique_id=unique_id)
                continue
            self.graph.add_asset(
                source_name,
                asset_type="ingestion",
                status="unknown",
            )

        nodes = manifest.get("nodes", {})
        sources = manifest.get("sources", {})

        for unique_id, node in nodes.items():
            if unique_id.startswith("model."):
                model_name = node.get("name")
                if not model_name:
                    logger.warning("dbt_model_name_missing", unique_id=unique_id)
                    continue

                for dep_id in node.get("depends_on", {}).get("nodes", []):
                    if dep_id.startswith("model."):
                        dep_node = nodes.get(dep_id)
                        if dep_node is None:
                            logger.warning("dbt_dependency_node_missing", dependency_id=dep_id)
                            continue
                        dep_name = dep_node.get("name")
                        if dep_name:
                            self.graph.add_edge(dep_name, model_name)
                    elif dep_id.startswith("source."):
                        source = sources.get(dep_id)
                        if source is None:
                            logger.warning("dbt_source_node_missing", source_id=dep_id)
                            continue
                        src_name_part = source.get("source_name") or ""
                        tbl_name_part = source.get("name") or ""
                        source_name = f"{src_name_part}.{tbl_name_part}".strip(".")
                        if source_name:
                            self.graph.add_edge(source_name, model_name)

        logger.info(
            "dbt_lineage_extracted",
            asset_count=len(self.graph.assets),
            edge_count=sum(len(v) for v in self.graph.edges.values()),
        )

    @log_extraction_errors("Iceberg")
    def extract_from_iceberg(
        self,
        nessie_tables: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Add Iceberg tables from Nessie to the lineage graph."""
        for namespace, tables in nessie_tables.items():
            for table in tables:
                table_name = table.get("name")
                if not table_name:
                    logger.debug(
                        "iceberg_table_name_missing",
                        namespace=namespace,
                    )
                    continue
                full_name = f"{namespace}.{table_name}"

                self.graph.add_asset(
                    full_name,
                    asset_type="ingestion",
                    status="unknown",
                )

        logger.info("iceberg_tables_extracted", table_count=len(self.graph.assets))

    def build_publishing_lineage(
        self,
        manifest: dict[str, Any],
        postgres_schema: str,
    ) -> dict[str, list[str]]:
        """Build a source -> published-tables mapping.

        Treats dbt models in postgres_schema as "published" tables and maps
        each ingestion source to its downstream published tables.
        """
        published_models: set[str] = set()
        for unique_id, node in manifest.get("nodes", {}).items():
            if not unique_id.startswith("model."):
                continue
            if node.get("schema") == postgres_schema and node.get("name"):
                published_models.add(node["name"])

        if not published_models:
            return {}

        lineage: dict[str, list[str]] = {}
        for _unique_id, source in manifest.get("sources", {}).items():
            source_fqn = f"{source.get('source_name')}.{source.get('name')}"
            downstream = self.graph.get_downstream(source_fqn)
            published_downstream = sorted(a for a in downstream if a in published_models)
            if published_downstream:
                lineage[source_fqn] = published_downstream

        return lineage

    def publish_to_openmetadata(
        self,
        om_client: Any,  # OpenMetadataClient
        include_edges: bool = True,
    ) -> dict[str, int]:
        """Publish lineage edges to OpenMetadata.

        Returns statistics with 'edges_published' and 'failed' counts;
        individual edge failures are logged and counted, not raised.
        """
        stats = {"edges_published": 0, "failed": 0}

        if not include_edges:
            return stats

        try:
            for from_asset, to_assets in self.graph.edges.items():
                for to_asset in to_assets:
                    try:
                        om_client.create_lineage(from_asset, to_asset)
                        stats["edges_published"] += 1
                    except Exception as exc:
                        logger.error(
                            "lineage_edge_publish_failed",
                            from_asset=from_asset,
                            to_asset=to_asset,
                            error=str(exc),
                        )
                        stats["failed"] += 1

            logger.info(
                "lineage_publish_completed",
                edges_published=stats["edges_published"],
            )

        except Exception as exc:
            logger.error("lineage_publish_failed", error=str(exc))
            stats["failed"] += 1

        return stats

    def get_impact_analysis(self, asset_name: str) -> dict[str, Any]:
        """Return downstream impact analysis for an asset.

        The result carries 'affected_assets' and 'total_affected'.
        """
        affected = sorted(self.graph.get_downstream(asset_name))
        return {"affected_assets": affected, "total_affected": len(affected)}

    def export_lineage(self, format_type: str = "json") -> str:
        """Export the lineage graph as 'json', 'dot', or 'mermaid'.

        Raises ValueError for unsupported formats.
        """
        if format_type == "json":
            return self.graph.to_json()
        if format_type == "dot":
            return self.graph.to_dot()
        if format_type == "mermaid":
            return self.graph.to_mermaid()
        raise ValueError(f"Unsupported format: {format_type}")

    @staticmethod
    def _normalize_fqn(fqn: str) -> str:
        """Normalize an unqualified table name to ``default.<name>``."""
        return fqn if "." in fqn else f"default.{fqn}"
