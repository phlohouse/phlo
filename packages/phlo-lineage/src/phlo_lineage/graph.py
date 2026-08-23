"""Build and analyze asset lineage graphs.

This module provides graph-based analysis of data asset dependencies. It implements
a directed graph structure where nodes represent assets (tables, models, datasets)
and edges represent data flow relationships (source -> target).

The LineageGraph class supports:
    - Upstream dependency traversal (finding all sources)
    - Downstream impact analysis (finding all dependents)
    - Impact assessment with categorization
    - Multiple export formats (ASCII, DOT, Mermaid, JSON)

Graph Construction:
    Graphs are typically built from the persistent LineageStore rather than
    constructed manually. The get_lineage_graph() function provides a global
    singleton instance that loads from the database.

Example:
    >>> from phlo_lineage import get_lineage_graph
    >>> graph = get_lineage_graph()
    >>> upstream = graph.get_upstream("gold.fct_orders")
    >>> downstream = graph.get_downstream("bronze.orders")
    >>> impact = graph.get_impact("silver.stg_orders")

Export Formats:
    - ASCII: Human-readable tree visualization
    - DOT: Graphviz format for rendering diagrams
    - Mermaid: Markdown-compatible diagram syntax
    - JSON: Machine-readable serialization

"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional, Set

from phlo.logging import get_logger
from phlo_lineage.store import LineageStore, resolve_lineage_db_url_with_postgres_fallback

logger = get_logger(__name__)


@dataclass
class Asset:
    """Represents a single asset node in the lineage graph.

    An asset is any data object that participates in the pipeline - source tables,
    staging models, fact tables, dimension tables, published datasets, etc.

    Example:
        >>> asset = Asset(
        ...     name="silver.stg_orders",
        ...     asset_type="transform",
        ...     status="success",
        ...     description="Cleaned and deduplicated orders",
        ... )
    """

    name: str
    asset_type: str = "unknown"  # ingestion, transform, publish
    status: str = "unknown"  # success, warning, failure
    description: Optional[str] = None


@dataclass
class LineageGraph:
    """Directed graph representing asset dependencies and data lineage.

    The LineageGraph maintains two core data structures:
        1. assets: Dictionary mapping asset names to Asset objects
        2. edges: Dictionary mapping source assets to lists of target assets

    Edge direction follows data flow: an edge from A to B means data flows
    from A (source) to B (target), or B depends on A.

    Example:
        >>> graph = LineageGraph()
        >>> graph.add_edge("bronze.orders", "silver.stg_orders")
        >>> graph.add_edge("silver.stg_orders", "gold.fct_orders")
        >>>
        >>> # Find what depends on bronze.orders
        >>> downstream = graph.get_downstream("bronze.orders")
        >>> print(downstream)  # {'silver.stg_orders', 'gold.fct_orders'}
        >>>
        >>> # Find sources for gold.fct_orders
        >>> upstream = graph.get_upstream("gold.fct_orders")
        >>> print(upstream)  # {'silver.stg_orders', 'bronze.orders'}

    Thread Safety:
        LineageGraph instances are not thread-safe. In concurrent environments,
        external synchronization is required for mutation operations.
    """

    assets: dict[str, Asset] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_asset(self, name: str, asset_type: str = "unknown", status: str = "unknown") -> None:
        """Add an asset to the graph if it doesn't already exist.

        Idempotent operation - if the asset already exists, no changes are made.
        This allows edges to be added without pre-creating nodes.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_asset("bronze.orders", "ingestion", "success")
            >>> assert "bronze.orders" in graph.assets
        """
        if name not in self.assets:
            self.assets[name] = Asset(name=name, asset_type=asset_type, status=status)

    def add_edge(self, source: str, target: str) -> None:
        """Add a directed edge from source to target asset.

        Creates implicit asset nodes for both source and target if they don't
        exist. Duplicate edges (same source-target pair) are ignored.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_edge("bronze.orders", "silver.stg_orders")
            >>> assert "silver.stg_orders" in graph.edges["bronze.orders"]
        """
        self.add_asset(source)
        self.add_asset(target)
        if target not in self.edges[source]:
            self.edges[source].append(target)

    def get_upstream(self, asset_name: str, depth: Optional[int] = None) -> Set[str]:
        """Traverse and return all upstream assets (dependencies/sources).

        Performs a breadth-first search from the starting asset to find all
        assets that feed data into it, directly or indirectly.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_edge("bronze.orders", "silver.stg_orders")
            >>> graph.add_edge("silver.stg_orders", "gold.fct_orders")
            >>>
            >>> # Full upstream
            >>> upstream = graph.get_upstream("gold.fct_orders")
            >>> print(upstream)  # {'bronze.orders', 'silver.stg_orders'}
            >>>
            >>> # Direct only
            >>> direct = graph.get_upstream("gold.fct_orders", depth=1)
            >>> print(direct)  # {'silver.stg_orders'}
        """
        upstream = set()
        visited = set()
        queue = deque([(asset_name, 0)])

        while queue:
            current, current_depth = queue.popleft()

            if current in visited:
                continue

            visited.add(current)

            # Edges are stored forward-only, so finding parents requires a full
            # scan of every edge list. Acceptable because graphs are small;
            # get_downstream uses the direct adjacency lookup instead.
            for source, targets in self.edges.items():
                if current in targets:
                    upstream.add(source)

                    if depth is None or current_depth < depth:
                        queue.append((source, current_depth + 1))

        return upstream

    def get_downstream(self, asset_name: str, depth: Optional[int] = None) -> Set[str]:
        """Traverse and return all downstream assets (dependents).

        Performs a breadth-first search from the starting asset to find all
        assets that depend on it, directly or indirectly.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_edge("bronze.orders", "silver.stg_orders")
            >>> graph.add_edge("silver.stg_orders", "gold.fct_orders")
            >>>
            >>> # Full downstream
            >>> downstream = graph.get_downstream("bronze.orders")
            >>> print(downstream)  # {'silver.stg_orders', 'gold.fct_orders'}
            >>>
            >>> # Direct only
            >>> direct = graph.get_downstream("bronze.orders", depth=1)
            >>> print(direct)  # {'silver.stg_orders'}
        """
        downstream = set()
        visited = set()
        queue = deque([(asset_name, 0)])

        while queue:
            current, current_depth = queue.popleft()

            if current in visited:
                continue

            visited.add(current)

            # Find all assets that current points to
            for target in self.edges.get(current, []):
                downstream.add(target)

                if depth is None or current_depth < depth:
                    queue.append((target, current_depth + 1))

        return downstream

    def get_impact(self, asset_name: str) -> dict:
        """Analyze the downstream impact of changes to an asset.

        Calculates metrics about how many and what types of assets would be
        affected by a failure or schema change in the specified asset.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_edge("bronze.orders", "silver.stg_orders")
            >>> graph.add_asset("gold.fct_orders", "publish")
            >>> graph.add_edge("silver.stg_orders", "gold.fct_orders")
            >>>
            >>> impact = graph.get_impact("bronze.orders")
            >>> print(impact["publishing_affected"])  # True
            >>> print(len(impact["affected_assets"]))  # 2

        Use Case:
            This method is useful for:
            - Pre-deployment impact assessment
            - Data incident scope evaluation
            - Change management approval workflows
        """
        downstream = self.get_downstream(asset_name)

        # Categorize by type
        impact = {
            "direct_count": 0,
            "indirect_count": len(downstream) - len(self.get_downstream(asset_name, depth=1)),
            "publishing_affected": False,
            "affected_assets": list(downstream),
        }

        # Check if any publishing assets are affected
        for asset in downstream:
            if self.assets.get(asset, Asset("")).asset_type == "publish":
                impact["publishing_affected"] = True

        return impact

    def to_ascii_tree(
        self, asset_name: str, direction: str = "both", depth: Optional[int] = None
    ) -> str:
        """Generate a human-readable ASCII tree representation of lineage.

        Creates a visual tree diagram showing upstream dependencies and/or
        downstream dependents of the specified asset.

        Example:
            >>> graph = LineageGraph()
            >>> graph.add_edge("bronze.orders", "silver.stg_orders")
            >>> print(graph.to_ascii_tree("silver.stg_orders", direction="upstream"))
            silver.stg_orders
            ├── [upstream]
            │   └── bronze.orders (ingestion)

        Visual Elements:
            - ├── indicates more siblings follow
            - └── indicates last sibling
            - │   shows branch continuation
            - [upstream] and [downstream] label sections
            - Status icons: ✓ (success) or ✗ (failure/warning)
        """
        lines = []
        lines.append(asset_name)

        if direction in ["upstream", "both"]:
            upstream = self.get_upstream(asset_name, depth=depth)
            if upstream:
                lines.append("├── [upstream]")
                for asset in sorted(upstream):
                    prefix = (
                        "│   "
                        if direction == "both" and self.get_downstream(asset_name, depth=depth)
                        else "    "
                    )
                    asset_obj = self.assets.get(asset, Asset(asset))
                    lines.append(f"{prefix}└── {asset} ({asset_obj.asset_type})")

        if direction in ["downstream", "both"]:
            downstream = self.get_downstream(asset_name, depth=depth)
            if downstream:
                prefix = "└── " if direction == "both" else "├── "
                label = "[downstream]" if direction == "both" else "[downstream]"
                lines.append(f"{prefix}{label}")

                for i, asset in enumerate(sorted(downstream)):
                    is_last = i == len(downstream) - 1
                    tree_prefix = "    " if is_last else "│   "
                    branch_prefix = "└── " if is_last else "├── "

                    asset_obj = self.assets.get(asset, Asset(asset))
                    status_icon = "✓" if asset_obj.status == "success" else "✗"

                    lines.append(
                        f"{tree_prefix}{branch_prefix}[{status_icon}] {asset} ({asset_obj.asset_type})"
                    )

        return "\n".join(lines)

    def to_dot(self) -> str:
        """Generate Graphviz DOT format representation of the graph.

        DOT is the native format for Graphviz, a widely-used graph visualization
        tool. The output can be rendered to PNG, SVG, PDF, and other formats.

        Example:
            >>> dot = graph.to_dot()
            >>> with open("lineage.dot", "w") as f:
            ...     f.write(dot)
            >>> # Render: dot -Tpng lineage.dot -o lineage.png

        Styling:
            - Nodes are styled boxes with colors by asset_type:
              - lightblue: ingestion
              - lightgreen: transform
              - lightcoral: publish
              - lightgray: unknown
            - Border colors indicate status:
              - green: success
              - orange: warning
              - red: failure
              - gray: unknown
            - Layout direction is Left-to-Right (rankdir="LR")

        See Also:
            https://graphviz.org/documentation/ for Graphviz documentation.
        """
        lines = ["digraph {", '  rankdir="LR";']

        # Add nodes with styling
        for asset_name, asset in self.assets.items():
            color = {
                "ingestion": "lightblue",
                "transform": "lightgreen",
                "publish": "lightcoral",
                "unknown": "lightgray",
            }.get(asset.asset_type, "lightgray")

            status_color = {
                "success": "green",
                "warning": "orange",
                "failure": "red",
                "unknown": "gray",
            }.get(asset.status, "gray")

            lines.append(
                f'  "{asset_name}" [label="{asset_name}", '
                f'shape="box", style="filled", fillcolor="{color}", '
                f'color="{status_color}", penwidth="2"];'
            )

        # Add edges
        for source, targets in self.edges.items():
            for target in targets:
                lines.append(f'  "{source}" -> "{target}";')

        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        """Generate Mermaid diagram format for documentation integration.

        Mermaid is a markdown-native diagram syntax supported by GitHub,
        GitLab, Notion, and many other documentation platforms.

        Example:
            >>> mermaid = graph.to_mermaid()
            >>> print("```mermaid")
            >>> print(mermaid)
            >>> print("```")

        Styling:
            - Node shapes indicate asset_type:
              - [({name} - Ingestion)]: Cylinder for ingestion
              - [{name} - Transform]: Box for transform
              - ({name} - Publish): Rounded for publish
              - [{name}]: Default box
            - Asset names are converted to safe identifiers by replacing
              hyphens and dots with underscores.

        See Also:
            https://mermaid.js.org/ for Mermaid syntax documentation.
        """
        lines = ["graph TD"]

        # Add nodes
        for asset_name, asset in self.assets.items():
            shape = {
                "ingestion": "[({} - Ingestion)]",
                "transform": "[{} - Transform]",
                "publish": "({} - Publish)",
                "unknown": "[{}]",
            }.get(asset.asset_type, "[{}]")

            lines.append(f'  {self._safe_id(asset_name)}"{shape.format(asset_name)}"')

        # Add edges
        for source, targets in self.edges.items():
            for target in targets:
                lines.append(f"  {self._safe_id(source)} --> {self._safe_id(target)}")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Generate JSON serialization of the graph.

        Produces a machine-readable representation suitable for programmatic
        consumption, API responses, or caching.

        Example:
            >>> json_str = graph.to_json()
            >>> import json
            >>> data = json.loads(json_str)
            >>> print(data["assets"]["bronze.orders"]["type"])
            'ingestion'
        """
        data = {
            "assets": {
                name: {
                    "type": asset.asset_type,
                    "status": asset.status,
                    "description": asset.description,
                }
                for name, asset in self.assets.items()
            },
            "edges": dict(self.edges),
        }
        return json.dumps(data, indent=2)

    @staticmethod
    def _safe_id(name: str) -> str:
        """Convert asset name to a Mermaid-safe identifier.

        Mermaid identifiers cannot contain hyphens or dots. This method
        replaces them with underscores for compatibility.

        Example:
            >>> LineageGraph._safe_id("bronze.stg-orders")
            'bronze_stg_orders'
            >>> LineageGraph._safe_id("silver.fct_orders")
            'silver_fct_orders'

        Note:
            This is an internal helper method used by to_mermaid().
        """
        return name.replace("-", "_").replace(".", "_")


# Global lineage graph instance
_lineage_graph: Optional[LineageGraph] = None


def get_lineage_graph() -> LineageGraph:
    """Get or create the global LineageGraph singleton instance.

    This function provides a lazily-initialized global graph instance that
    loads from the persistent LineageStore on first access. Subsequent calls
    return the cached instance.

    Example:
        >>> from phlo_lineage import get_lineage_graph
        >>> graph = get_lineage_graph()
        >>> print(f"Assets: {len(graph.assets)}")

    Initialization:
        On first call, attempts to:
        1. Resolve the lineage database connection string
        2. Connect to PostgreSQL and load asset nodes
        3. Load asset edges to reconstruct the graph
        4. Handle errors gracefully (returns empty graph on failure)

    Thread Safety:
        The global instance is created lazily without explicit locking.
        In concurrent scenarios, multiple threads may briefly have different
        instances until the assignment completes.

    See Also:
        _build_lineage_from_store() for the loading implementation.
    """
    global _lineage_graph
    if _lineage_graph is None:
        _lineage_graph = _build_lineage_from_store()
    return _lineage_graph


def _build_lineage_from_store() -> LineageGraph:
    """Build a LineageGraph from the persistent PostgreSQL store.

    This internal function reconstructs the in-memory graph representation
    by querying the phlo.asset_lineage_nodes and phlo.asset_lineage_edges
    tables.
    """
    graph = LineageGraph()
    connection_string = resolve_lineage_db_url_with_postgres_fallback()
    if not connection_string:
        logger.debug("lineage_graph_init_skipped_database_unconfigured")
        return graph

    try:
        store = LineageStore(connection_string)
        for node in store.list_asset_nodes():
            graph.add_asset(
                node["asset_key"],
                asset_type=node.get("asset_type") or "unknown",
                status=node.get("status") or "unknown",
            )
            if node.get("description"):
                graph.assets[node["asset_key"]].description = node["description"]

        for edge in store.list_asset_edges():
            graph.add_edge(edge["source_asset"], edge["target_asset"])
    except Exception as exc:
        logger.warning("lineage_graph_store_load_failed", error=str(exc), exc_info=True)

    return graph
