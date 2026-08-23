"""Hasura metadata export, import and schema management.

This module provides classes and functions for managing Hasura metadata
lifecycle operations including export, import, diff calculation, and
version control integration.

Classes:
    HasuraMetadataSync: Manages metadata export/import and version control.

Functions:
    export_metadata: Convenience function to export metadata.
    apply_metadata: Convenience function to apply metadata from file.

Example:
    >>> from phlo_hasura.sync import HasuraMetadataSync, export_metadata
    >>> syncer = HasuraMetadataSync()
    >>> metadata = syncer.export_metadata("backup.json")
    >>> syncer.import_metadata("backup.json")

"""

import json
from pathlib import Path
from typing import Any, Optional

from phlo_hasura.client import HasuraClient
from phlo.logging import get_logger

logger = get_logger(__name__)


class HasuraMetadataSync:
    """Manage Hasura metadata export/import and version control.

    Exports metadata to files, imports from files, calculates diffs between
    metadata versions, and generates reports. ``client`` is the HasuraClient
    used for API operations.

    Example:
        >>> syncer = HasuraMetadataSync()
        >>> syncer.export_metadata("backup.json")
        >>> current = syncer.export_metadata()
        >>> diff = syncer.get_diff(old_metadata, current)
    """

    def __init__(self, client: Optional[HasuraClient] = None):
        """Initialize metadata sync.

        ``client`` defaults to a new HasuraClient built with default settings.

        Example:
            >>> syncer = HasuraMetadataSync()
            >>> custom_syncer = HasuraMetadataSync(HasuraClient())
        """
        self.client = client or HasuraClient()

    def export_metadata(self, output_path: Optional[str | Path] = None) -> dict[str, Any]:
        """Export Hasura metadata, optionally saving it to a JSON file.

        Raise OSError when writing the output file fails.

        Example:
            >>> syncer = HasuraMetadataSync()
            >>> metadata = syncer.export_metadata()
            >>> syncer.export_metadata("backup.json")
        """
        metadata = self.client.export_metadata()

        if output_path:
            output_path = Path(output_path)
            with open(output_path, "w") as f:
                json.dump(metadata, f, indent=2)

        return metadata

    def import_metadata(self, input_path: str | Path) -> dict[str, Any]:
        """Import Hasura metadata from file, replacing the current metadata.

        Raise FileNotFoundError when the input file does not exist,
        json.JSONDecodeError when it contains invalid JSON, and
        requests.RequestException when the API call fails.

        Example:
            >>> syncer = HasuraMetadataSync()
            >>> syncer.import_metadata("backup.json")
        """
        input_path = Path(input_path)

        with open(input_path) as f:
            metadata = json.load(f)

        return self.client.apply_metadata(metadata)

    def merge_metadata(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Merge two metadata dictionaries, with ``override`` winning over ``base``.

        Combines top-level keys and sources.

        Example:
            >>> base = syncer.export_metadata()
            >>> override = {"version": 3}
            >>> merged = syncer.merge_metadata(base, override)
        """
        merged = base.copy()

        # Merge top-level keys
        for key in ["version", "metadata"]:
            if key in override:
                merged[key] = override[key]

        # Merge sources (custom types, functions, etc.)
        if "sources" in override:
            # Replace entire sources list for simplicity
            merged["sources"] = override["sources"]

        return merged

    def get_diff(self, current: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
        """Calculate diff between current and desired metadata.

        Returns a structured diff with ``sources``, ``tables``, ``relationships``,
        and ``permissions`` entries; each maps ``added``/``removed`` (and
        ``modified`` where applicable) to name lists.

        Example:
            >>> current = syncer.export_metadata()
            >>> desired = json.load(open("target.json"))
            >>> diff = syncer.get_diff(current, desired)
            >>> print(f"Tables to add: {len(diff['tables']['added'])}")
        """
        diff = {
            "sources": {"added": [], "removed": [], "modified": []},
            "tables": {"added": [], "removed": [], "modified": []},
            "relationships": {"added": [], "removed": []},
            "permissions": {"added": [], "removed": []},
        }

        # Track current tables and sources
        current_sources = {s.get("name"): s for s in current.get("sources", [])}
        desired_sources = {s.get("name"): s for s in desired.get("sources", [])}

        # Check for added/removed sources
        for name in desired_sources:
            if name not in current_sources:
                diff["sources"]["added"].append(name)

        for name in current_sources:
            if name not in desired_sources:
                diff["sources"]["removed"].append(name)

        # Check table differences
        current_tables = self._extract_tables(current)
        desired_tables = self._extract_tables(desired)

        current_table_set = set(current_tables.keys())
        desired_table_set = set(desired_tables.keys())

        diff["tables"]["added"] = list(desired_table_set - current_table_set)
        diff["tables"]["removed"] = list(current_table_set - desired_table_set)

        # Check for modified tables
        for table_path in current_table_set & desired_table_set:
            if current_tables[table_path] != desired_tables[table_path]:
                diff["tables"]["modified"].append(table_path)

        # Check relationship and permission differences
        current_rels = self._extract_relationships(current)
        desired_rels = self._extract_relationships(desired)

        diff["relationships"]["added"] = list(set(desired_rels) - set(current_rels))
        diff["relationships"]["removed"] = list(set(current_rels) - set(desired_rels))

        return diff

    def _extract_tables(self, metadata: dict[str, Any]) -> dict[str, dict]:
        """Extract tracked tables from metadata by fully qualified path.

        Only tables under sources named ``default`` are included.

        Example:
            >>> tables = syncer._extract_tables(metadata)
            >>> print(list(tables.keys()))
            ['api.orders', 'api.customers']
        """
        tables = {}

        for source in metadata.get("sources", []):
            if source.get("name") != "default":
                continue

            for table in source.get("tables", []):
                schema = table.get("table", {}).get("schema", "public")
                name = table["table"]["name"]
                table_path = f"{schema}.{name}"
                tables[table_path] = table

        return tables

    def _extract_relationships(self, metadata: dict[str, Any]) -> list[tuple]:
        """Extract object and array relationships from metadata as tuples.

        Each tuple is ``(table_path, relationship_name, relationship_type)``.

        Example:
            >>> rels = syncer._extract_relationships(metadata)
            >>> for table, name, type_ in rels:
            ...     print(f"{table}.{name} ({type_})")
        """
        rels = []

        for source in metadata.get("sources", []):
            if source.get("name") != "default":
                continue

            for table in source.get("tables", []):
                schema = table.get("table", {}).get("schema", "public")
                table_name = table["table"]["name"]

                for rel in table.get("object_relationships", []):
                    rel_name = rel.get("name")
                    rels.append((f"{schema}.{table_name}", rel_name, "object"))

                for rel in table.get("array_relationships", []):
                    rel_name = rel.get("name")
                    rels.append((f"{schema}.{table_name}", rel_name, "array"))

        return rels

    def generate_diff_report(self, current: dict[str, Any], desired: dict[str, Any]) -> str:
        """Generate a human-readable report of differences between two states.

        Example:
            >>> report = syncer.generate_diff_report(current, desired)
            >>> print(report)
            Hasura Metadata Diff Report
            ============================
            Tables to track: 5
              + api.orders
              + api.customers
            ...
        """
        diff = self.get_diff(current, desired)

        lines = ["Hasura Metadata Diff Report", "=" * 60]

        # Sources
        if diff["sources"]["added"]:
            lines.append(f"\nSources to add: {len(diff['sources']['added'])}")
            for source in diff["sources"]["added"]:
                lines.append(f"  + {source}")

        if diff["sources"]["removed"]:
            lines.append(f"\nSources to remove: {len(diff['sources']['removed'])}")
            for source in diff["sources"]["removed"]:
                lines.append(f"  - {source}")

        # Tables
        if diff["tables"]["added"]:
            lines.append(f"\nTables to track: {len(diff['tables']['added'])}")
            for table in sorted(diff["tables"]["added"]):
                lines.append(f"  + {table}")

        if diff["tables"]["removed"]:
            lines.append(f"\nTables to untrack: {len(diff['tables']['removed'])}")
            for table in sorted(diff["tables"]["removed"]):
                lines.append(f"  - {table}")

        if diff["tables"]["modified"]:
            lines.append(f"\nTables to modify: {len(diff['tables']['modified'])}")
            for table in sorted(diff["tables"]["modified"]):
                lines.append(f"  ~ {table}")

        # Relationships
        if diff["relationships"]["added"]:
            lines.append(f"\nRelationships to add: {len(diff['relationships']['added'])}")
            for table, rel, rel_type in sorted(diff["relationships"]["added"]):
                lines.append(f"  + {table}.{rel} ({rel_type})")

        if diff["relationships"]["removed"]:
            lines.append(f"\nRelationships to remove: {len(diff['relationships']['removed'])}")
            for table, rel, rel_type in sorted(diff["relationships"]["removed"]):
                lines.append(f"  - {table}.{rel} ({rel_type})")

        return "\n".join(lines)

    def reload_metadata(self) -> None:
        """Reload metadata from the underlying database.

        Forces Hasura to reload its metadata, which is useful after direct database
        schema changes. Raise requests.RequestException when the API call fails.

        Example:
            >>> syncer.reload_metadata()  # After manual DB changes
        """
        self.client.reload_metadata()


def export_metadata(output_path: Optional[str] = None, verbose: bool = True) -> str:
    """Export Hasura metadata without instantiating HasuraMetadataSync.

    Writes to ``output_path`` when given, otherwise returns the metadata as a
    JSON string. Prints progress messages unless ``verbose`` is False.

    Example:
        >>> export_metadata("backup.json")
        'backup.json'
        >>> json_str = export_metadata()
        >>> print(json_str[:50])
        {"version": 3, "sources": [...]}
    """
    if verbose:
        logger.info("Exporting Hasura metadata...")

    syncer = HasuraMetadataSync()
    metadata = syncer.export_metadata(output_path)

    if output_path:
        if verbose:
            logger.info("✓ Metadata exported to %s", output_path)
        return output_path
    else:
        if verbose:
            logger.info("✓ Metadata exported")
        return json.dumps(metadata, indent=2)


def apply_metadata(input_path: str, verbose: bool = True) -> None:
    """Apply metadata from a JSON file without instantiating HasuraMetadataSync.

    Raise FileNotFoundError when the input file is missing or
    requests.RequestException when the API call fails.

    Example:
        >>> apply_metadata("backup.json")
        >>> apply_metadata("production-metadata.json", verbose=False)
    """
    if verbose:
        logger.info("Applying metadata from %s...", input_path)

    syncer = HasuraMetadataSync()
    syncer.import_metadata(input_path)

    if verbose:
        logger.info("✓ Metadata applied")
