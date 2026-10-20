"""Exercise branch history and diff against the supported Nessie server."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import httpx
import pytest
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
        name = f"api-contract-{uuid4().hex[:8]}"
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
        history = asyncio.run(branches._history(name, head_hash, limit=260))
        assert len(history) == 260
        assert len({commit.hash for commit in history}) == 260

        source = branches.BranchReference(
            env="prod", name=name, type="BRANCH", hash=head_hash, protected=False
        )
        target = branches.BranchReference(
            env="prod", name="main", type="BRANCH", hash=initial_hash, protected=True
        )
        comparison = asyncio.run(branches._comparison(source, source, "prod"))
        diff = asyncio.run(branches._diff(source, target, "prod"))

        assert comparison.merge_base == head_hash
        assert comparison.ahead == comparison.behind == 0
        assert comparison.source_hash == comparison.target_hash == head_hash
        assert len(diff.items) == 260
        assert diff.source_hash == head_hash
        assert diff.target_hash == initial_hash
