"""Exercise installed authoring providers with generated container mount boundaries.

Only disposable local files and containers are created. This proves retained CSV,
generated workflow visibility and native SQL draft storage, not remote Dagster
reload, candidate materialisation, authenticated HTTP or Trino readback.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from phlo.cli.commands.services.utils import prepare_api_runtime_directories
from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery.services import ServiceDiscovery

AUTHOR = """
import hashlib, json
from pathlib import Path
from phlo_dlt.csv_upload import author_csv_upload, reviewed_rows
from phlo_transform import editor
root = Path('/app')
identity = 'a' * 32
data = b'id,label\\n7,retained\\n19,synthetic\\n'
retained = root / '.phlo/uploads' / identity
retained.mkdir()
(retained / 'source.csv').write_bytes(data)
manifest = dict(upload_id=identity, table='image_acceptance',
    checksum=hashlib.sha256(data).hexdigest(), row_count=2,
    record_key='_record_id', actor='image-check', candidate_ref='candidate-check',
    columns=[dict(column=name, name=name, type=kind, nullable=False)
             for name, kind in [('id', 'integer'), ('label', 'string')]])
result = author_csv_upload(root, {'manifest': manifest})
rows = reviewed_rows(root, manifest)
assert [(r['id'], r['label']) for r in rows] == [(7, 'retained'), (19, 'synthetic')]
source = editor.read_model_source(root, 'acceptance.example')
draft = editor.save_model_draft(root, 'acceptance.example', actor='image-check',
    env='sandbox', ref='main', sql='SELECT 19 AS id',
    source_digest=source['source_digest'], workspace_digest=source['workspace_digest'])
saved = editor.read_model_draft(root, 'acceptance.example', draft_id=draft['draft_id'],
    revision=1, actor='image-check', env='sandbox', ref='main')
assert saved['sql'] == 'SELECT 19 AS id'
assert (root / 'transforms/transforms/example.sql').read_text() == 'SELECT 7 AS id\\n'
for path in ['phlo.yaml', 'workflows/unrelated.py', 'contracts/unrelated.json']:
    try:
        (root / path).write_text('must not write')
    except OSError:
        pass
    else:
        raise AssertionError(f'Project definition was writable: {path}')
print(json.dumps({'csv_rows': len(rows), 'generated_files': result['files'],
                  'draft_revision': saved['revision'], 'unrelated_writes': 'denied'}))
"""

WORKER = """
import json, runpy
from pathlib import Path
from phlo_dlt.csv_upload import reviewed_rows
from phlo_dlt.decorator import get_ingestion_assets
root = Path('/app')
contract = root / 'contracts/uploads' / ('a' * 32 + '.json')
manifest = json.loads(contract.read_text())
assert [r['id'] for r in reviewed_rows(root, manifest)] == [7, 19]
runpy.run_path(str(root / 'workflows/uploads' / ('a' * 32 + '.py')))
assert len(get_ingestion_assets()) == 1
print('Shared worker: retained CSV read and generated ingestion asset registered')
"""


def main() -> None:
    """Run disposable probes using installed images and the current service generator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Provider/native-enabled API image")
    parser.add_argument("--worker-image", help="Optional installed Dagster image with phlo-dlt")
    args = parser.parse_args()
    if not hasattr(os, "getuid") or os.getuid() == 0:
        parser.error("Run this UID-sharing probe as a non-root POSIX project owner")
    discovery = ServiceDiscovery()
    service = discovery.get_service("phlo-api")
    if service is None:
        parser.error("Install phlo-api in the driver environment")
    with tempfile.TemporaryDirectory(prefix="phlo-authoring-image-") as temporary:
        root = Path(temporary)
        state = root / ".phlo"
        state.mkdir()
        prepare_api_runtime_directories(state, ["phlo-api"])
        (root / "phlo.yaml").write_text("name: image-acceptance\n")
        transforms = root / "transforms"
        (transforms / "transforms").mkdir(parents=True)
        (transforms / "phlo.toml").write_text('[transform]\ndefault_namespace="acceptance"\n')
        (transforms / "transforms/example.sql").write_text("SELECT 7 AS id\n")
        compose = yaml.safe_load(ComposeGenerator(discovery).generate_compose([service], state))
        mounts = []
        for volume in compose["services"]["phlo-api"]["volumes"]:
            if isinstance(volume, str):
                source, target, *mode = volume.split(":")
                mounts.extend(
                    [
                        "--mount",
                        f"type=bind,src={(state / source).resolve()},"
                        f"dst={target}" + (",readonly" if mode == ["ro"] else ""),
                    ]
                )
            else:
                assert volume["bind"]["create_host_path"] is False
                mounts.extend(
                    [
                        "--mount",
                        f"type=bind,src={(state / volume['source']).resolve()},"
                        f"dst={volume['target']}",
                    ]
                )
        identity = ["--user", f"{os.getuid()}:{os.getgid()}"]
        command = [
            "docker",
            "run",
            "--rm",
            *identity,
            *mounts,
            "-e",
            "PHLO_OBSERVATORY_SETTINGS_BACKEND=memory",
            "-e",
            "PHLO_TRANSFORM_ADAPTER=duckdb",
            "--entrypoint",
            "python",
            args.image,
        ]
        subprocess.run(command + ["-I", "-c", AUTHOR], check=True)
        worker = [
            "docker",
            "run",
            "--rm",
            *identity,
            "--mount",
            f"type=bind,src={root},dst=/app,readonly",
            "--mount",
            f"type=bind,src={state / 'logs'},dst=/app/.phlo/logs",
            "--entrypoint",
            "python",
            args.worker_image or args.image,
            "-I",
            "-c",
            WORKER,
        ]
        subprocess.run(worker, check=True)
        print("PASS: installed-image authoring boundary (not full service acceptance)")


if __name__ == "__main__":
    main()
