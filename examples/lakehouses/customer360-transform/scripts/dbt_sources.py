"""Derive ingestion asset bindings from a dbt project's source metadata.

Phlo Transform resolves sources to physical relations; Phlo orchestrators also
need the asset that produces each relation so the ingestion -> transform edge
survives a provider switch. dbt source metadata already records that binding
(``meta.phlo_asset_key``), so the migration script carries it into the native
workspace's ``[phlo_transform.sources]`` table.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def _model_paths(dbt_project: Path) -> list[Path]:
    config = yaml.safe_load((dbt_project / "dbt_project.yml").read_text(encoding="utf-8")) or {}
    paths = config.get("model-paths") or ["models"]
    return [dbt_project / str(path) for path in paths]


def _source_files(model_root: Path) -> list[Path]:
    if not model_root.exists():
        return []
    return sorted(
        {*model_root.rglob("*.yml"), *model_root.rglob("*.yaml")},
        key=lambda path: str(path),
    )


def source_asset_map(dbt_project: Path) -> dict[str, str]:
    """Return ``{"raw.table": "ingestion_asset"}`` for every declared source."""

    mapping: dict[str, str] = {}
    for model_root in _model_paths(dbt_project):
        for path in _source_files(model_root):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for source in payload.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                schema = str(source.get("schema") or "")
                source_name = str(source.get("name") or "")
                for table in source.get("tables") or []:
                    if not isinstance(table, dict):
                        continue
                    name = str(table.get("name") or "")
                    identifier = str(table.get("identifier") or name)
                    meta = table.get("meta") or {}
                    asset = meta.get("phlo_asset_key") or meta.get("asset_key")
                    if asset is None and (
                        source_name == "dagster_assets" or source_name.startswith("raw_")
                    ):
                        asset = f"dlt_{name}"
                    if not asset or not schema:
                        continue
                    mapping[f"{schema}.{identifier}"] = str(asset)
    return mapping


def apply_source_assets(phlo_toml: Path, mapping: dict[str, str]) -> None:
    """Replace ``[phlo_transform.sources]`` in ``phlo.toml`` with ``mapping``."""

    lines = phlo_toml.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[phlo_transform.sources]":
            skipping = True
            continue
        if skipping and stripped.startswith("["):
            skipping = False
        if not skipping:
            kept.append(line)
    text = "\n".join(kept).rstrip()
    if mapping:
        text += "\n\n[phlo_transform.sources]\n"
        for source, asset in sorted(mapping.items()):
            text += f"{json.dumps(source)} = {json.dumps(asset)}\n"
    phlo_toml.write_text(text.rstrip() + "\n", encoding="utf-8")


__all__ = ["apply_source_assets", "source_asset_map"]
