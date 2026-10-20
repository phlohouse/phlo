"""Exercise branch history and diff against the supported Nessie server."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer

from phlo_api.api import v1_branch_workflows as branches


@pytest.mark.integration
def test_branch_history_and_diff_use_nessie_v2_paging_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        DockerContainer("ghcr.io/projectnessie/nessie:0.108.3")
        .with_env("NESSIE_VERSION_STORE_TYPE", "IN_MEMORY")
        .with_exposed_ports(19120) as nessie
    ):
        base_url = f"http://127.0.0.1:{nessie.get_exposed_port(19120)}"
        for _ in range(90):
            try:
                if httpx.get(f"{base_url}/api/v2/config", timeout=1).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("Nessie 0.108.3 did not become ready")

        main = httpx.get(f"{base_url}/api/v2/trees/main", timeout=5).json()["reference"]
        initial_hash = main["hash"]
        name = f"prod-contract-{uuid4().hex[:8]}"
        created = httpx.post(
            f"{base_url}/api/v2/trees",
            params={"name": name, "type": "BRANCH"},
            json={"type": "BRANCH", "name": "main", "hash": initial_hash},
            timeout=5,
        )
        created.raise_for_status()

        head_hash = initial_hash
        for index in range(260):
            response = httpx.post(
                f"{base_url}/api/v2/trees/{name}@{head_hash}/history/commit",
                json={
                    "commitMeta": {"message": f"contract commit {index}"},
                    "operations": [
                        {
                            "type": "PUT",
                            "key": {"elements": [f"table-{index}"]},
                            "content": {
                                "type": "ICEBERG_TABLE",
                                "metadataLocation": f"file:///metadata/{index}.json",
                                "snapshotId": 1,
                                "schemaId": 1,
                                "specId": 0,
                                "sortOrderId": 0,
                            },
                        }
                    ],
                },
                timeout=5,
            )
            response.raise_for_status()
            head_hash = response.json()["targetBranch"]["hash"]

        monkeypatch.setattr(branches, "project_env_value", lambda _name: base_url)
        monkeypatch.setenv(
            "PHLO_V1_ENVIRONMENTS",
            json.dumps({"prod": {"dagster_location": "unused", "nessie_ref": "main"}}),
        )
        monkeypatch.setattr(
            branches,
            "require_scope",
            lambda _request, scope: {"subject": "contract-test", "scopes": [scope]},
        )
        api = FastAPI()
        api.include_router(branches.router, prefix="/api/v1")
        with TestClient(api) as client:
            first = client.get(f"/api/v1/branches/{name}/commits?env=prod&limit=100")
            assert first.status_code == 200, first.text
            assert first.json()["next_cursor"]
            second = client.get(
                f"/api/v1/branches/{name}/commits?env=prod&limit=100&after={first.json()['next_cursor']}"
            )
            assert second.status_code == 200, second.text
            assert second.json()["next_cursor"]
            third = client.get(
                f"/api/v1/branches/{name}/commits?env=prod&limit=100&after={second.json()['next_cursor']}"
            )
            assert third.status_code == 200, third.text
            pages = [first.json()["items"], second.json()["items"], third.json()["items"]]
            hashes = [commit["hash"] for page in pages for commit in page]
            assert [len(page) for page in pages] == [100, 100, 60]
            assert len(set(hashes)) == len(hashes) == 260
            comparison = client.get(f"/api/v1/branches/{name}/compare?env=prod&target={name}")
            assert comparison.status_code == 200, comparison.text
            assert comparison.json()["merge_base"] == head_hash
            assert comparison.json()["ahead"] == comparison.json()["behind"] == 0
            assert comparison.json()["source_hash"] == comparison.json()["target_hash"] == head_hash
            diff = client.get(f"/api/v1/branches/{name}/diff?env=prod&target=main")
            assert diff.status_code == 200, diff.text
            assert len(diff.json()["items"]) == 260
            assert diff.json()["source_hash"] == head_hash
            assert diff.json()["target_hash"] == initial_hash
