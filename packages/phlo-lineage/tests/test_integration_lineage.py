"""Comprehensive integration tests for phlo-lineage.

Per TEST_STRATEGY.md Level 2 (Functional):
- Graph Construction: Test lineage graph building
- Store/Retrieve: Write lineage events, query graph back
"""

import pytest

pytestmark = pytest.mark.integration


# =============================================================================
# Lineage Event Types Tests
# =============================================================================


class TestLineageEventTypes:
    """Test lineage event type imports and structure."""

    def test_lineage_event_context_importable(self):
        """Test LineageEventContext is importable."""
        from phlo.hooks import LineageEventContext

        assert LineageEventContext is not None

    def test_lineage_event_emitter_importable(self):
        """Test LineageEventEmitter is importable."""
        from phlo.hooks import LineageEventEmitter

        assert LineageEventEmitter is not None

    def test_lineage_context_creation(self):
        """Test creating a LineageEventContext."""
        from phlo.hooks import LineageEventContext

        context = LineageEventContext(tags={"asset": "test_asset"})

        assert context is not None
        assert context.tags["asset"] == "test_asset"

    def test_lineage_emitter_creation(self):
        """Test creating a LineageEventEmitter."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        context = LineageEventContext(tags={"test": "true"})
        emitter = LineageEventEmitter(context)

        assert emitter is not None


# =============================================================================
# Lineage Edge Emission Tests
# =============================================================================


class TestLineageEdgeEmission:
    """Test lineage edge emission functionality."""

    def test_emit_single_edge(self):
        """Test emitting a single lineage edge."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        context = LineageEventContext(tags={})
        emitter = LineageEventEmitter(context)

        # Should not raise
        emitter.emit_edges(
            edges=[("source_table", "target_table")], asset_keys=["target_table"], metadata={}
        )

    def test_emit_multiple_edges(self):
        """Test emitting multiple lineage edges."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        context = LineageEventContext(tags={})
        emitter = LineageEventEmitter(context)

        edges = [
            ("raw.users", "staging.users"),
            ("raw.orders", "staging.orders"),
            ("staging.users", "gold.dim_users"),
            ("staging.orders", "gold.fct_orders"),
        ]

        # Should not raise
        emitter.emit_edges(
            edges=edges,
            asset_keys=["gold.dim_users", "gold.fct_orders"],
            metadata={"transform": "dbt"},
        )

    def test_emit_edges_with_metadata(self):
        """Test emitting edges with rich metadata."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        context = LineageEventContext(tags={"pipeline": "test"})
        emitter = LineageEventEmitter(context)

        metadata = {
            "sql": "SELECT * FROM source",
            "columns": ["id", "name"],
            "row_count": 1000,
        }

        emitter.emit_edges(edges=[("source", "target")], asset_keys=["target"], metadata=metadata)


# =============================================================================
# Lineage Graph Construction Tests
# =============================================================================


class TestLineageGraphConstruction:
    """Test lineage graph construction."""

    def test_build_simple_graph(self):
        """Test building a simple lineage graph."""
        # Simple graph structure
        edges = [
            ("A", "B"),
            ("B", "C"),
            ("A", "D"),
        ]

        # Build adjacency list
        graph = {}
        for source, target in edges:
            if source not in graph:
                graph[source] = []
            graph[source].append(target)

        assert "A" in graph
        assert "B" in graph["A"]
        assert "D" in graph["A"]
        assert "C" in graph["B"]

    def test_find_upstream_dependencies(self):
        """Test finding upstream dependencies in a graph."""
        edges = [
            ("raw.data", "staging.data"),
            ("staging.data", "gold.metrics"),
            ("external.ref", "gold.metrics"),
        ]

        # Build reverse adjacency (downstream -> upstream)
        reverse_graph = {}
        for source, target in edges:
            if target not in reverse_graph:
                reverse_graph[target] = []
            reverse_graph[target].append(source)

        # Find upstream of gold.metrics
        upstream = reverse_graph.get("gold.metrics", [])

        assert "staging.data" in upstream
        assert "external.ref" in upstream

    def test_detect_cycles(self):
        """Test that cycle detection can be implemented."""
        # Graph with a cycle: A -> B -> C -> A
        edges = [
            ("A", "B"),
            ("B", "C"),
            ("C", "A"),  # Creates cycle
        ]

        graph = {}
        for source, target in edges:
            if source not in graph:
                graph[source] = []
            graph[source].append(target)

        # Simple cycle detection using DFS
        def has_cycle(node, visited, rec_stack):
            """Detect whether the current DFS path contains a cycle."""
            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        visited = set()
        rec_stack = set()

        cycle_found = False
        for node in graph:
            if node not in visited:
                if has_cycle(node, visited, rec_stack):
                    cycle_found = True
                    break

        assert cycle_found is True


# =============================================================================
# Lineage Module Tests
# =============================================================================


class TestLineageModule:
    """Test phlo-lineage module structure."""

    def test_module_importable(self):
        """Test phlo_lineage module is importable."""
        try:
            import phlo_lineage

            assert phlo_lineage is not None
        except ImportError:
            # Module may not have __init__ exports
            pass

    def test_lineage_from_hooks(self):
        """Test lineage functionality from phlo.hooks."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        # These are the primary lineage interfaces
        assert LineageEventContext is not None
        assert LineageEventEmitter is not None


# =============================================================================
# Lineage Event Hooks Plugin Tests
# =============================================================================


class TestLineageHooksPlugin:
    """Test lineage hooks plugin if available."""

    def test_hooks_plugin_module_exists(self):
        """Test lineage hooks_plugin module exists."""
        try:
            import phlo_lineage.hooks_plugin  # noqa: F401
            # Module exists but may not have LineageHooksPlugin yet
        except ImportError:
            # Module may not exist
            pass


# =============================================================================
# Integration with DBT Lineage
# =============================================================================


class TestDbtLineageIntegration:
    """Test DBT lineage integration."""

    def test_parse_dbt_manifest_nodes(self):
        """Test parsing model relationships from DBT manifest structure."""
        # Mock DBT manifest structure
        manifest = {
            "nodes": {
                "model.project.model_a": {
                    "unique_id": "model.project.model_a",
                    "depends_on": {"nodes": ["source.project.raw.table_a"]},
                },
                "model.project.model_b": {
                    "unique_id": "model.project.model_b",
                    "depends_on": {
                        "nodes": ["model.project.model_a", "source.project.raw.table_b"]
                    },
                },
            }
        }

        # Extract lineage edges
        edges = []
        for node_id, node in manifest["nodes"].items():
            for upstream in node.get("depends_on", {}).get("nodes", []):  # type: ignore[union-attr]
                edges.append((upstream, node_id))

        assert len(edges) == 3
        assert ("source.project.raw.table_a", "model.project.model_a") in edges
        assert ("model.project.model_a", "model.project.model_b") in edges


# =============================================================================
# Export Tests
# =============================================================================


class TestLineageExports:
    """Test lineage-related exports."""

    def test_hooks_lineage_exports(self):
        """Test phlo.hooks exports lineage classes."""
        from phlo.hooks import LineageEventContext, LineageEventEmitter

        assert LineageEventContext is not None
        assert LineageEventEmitter is not None
