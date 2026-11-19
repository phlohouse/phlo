"""HTTP contracts for Observatory notebooks: files, kernels and query binding."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal
from phlo_api import notebook_files
from phlo_api.api import v1_notebooks, v1_query
from phlo_api.errors import PhloApiError, error_envelope
from phlo_api.notebook_kernels import KernelRegistry, kernel_environment

SAMPLE = {
    "cells": [
        {"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["# Trials\n"]},
        {
            "cell_type": "code",
            "id": "q1",
            "execution_count": 3,
            "metadata": {"phlo": {"kind": "sql", "engine": "trino", "name": "trials"}},
            "outputs": [{"output_type": "stream", "name": "stdout", "text": "old"}],
            "source": ["SELECT 1"],
        },
        {
            "cell_type": "code",
            "id": "py1",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": "x = 1",
        },
    ],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


@pytest.fixture
def actor() -> dict[str, str]:
    return {"subject": "analyst"}


@pytest.fixture
def notebook_api(monkeypatch: pytest.MonkeyPatch, tmp_path, actor):
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps({"prod": {"dagster_location": "prod", "nessie_ref": "main"}}),
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_NOTEBOOK_KERNELS", raising=False)
    monkeypatch.setattr(
        v1_notebooks,
        "get_request_principal",
        lambda _request: AuthPrincipal(subject=actor["subject"], principal_type="user", groups=()),
    )
    (tmp_path / "notebooks" / "market").mkdir(parents=True)
    (tmp_path / "notebooks" / "market" / "trials.ipynb").write_text(json.dumps(SAMPLE))
    app = FastAPI()
    app.include_router(v1_notebooks.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def api_error(_request: Request, exc: PhloApiError):
        return JSONResponse(error_envelope(exc), status_code=exc.status_code)

    with TestClient(app) as client:
        yield client, tmp_path


def test_lists_and_reads_notebooks_as_phlo_cells(notebook_api):
    client, _ = notebook_api
    listing = client.get("/api/v1/notebooks?env=prod").json()
    assert [item["path"] for item in listing["items"]] == ["market/trials.ipynb"]
    document = client.get("/api/v1/notebooks/content?env=prod&path=market/trials.ipynb").json()
    assert document["nessie_ref"] == "main"
    assert [(c["kind"], c["engine"], c["name"]) for c in document["cells"]] == [
        ("markdown", None, None),
        ("sql", "trino", "trials"),
        ("python", None, None),
    ]


@pytest.mark.parametrize(
    "path",
    ["../secrets.ipynb", "/etc/passwd.ipynb", "market/trials.py", "a\\b.ipynb", ".x/a.ipynb"],
)
def test_rejects_paths_outside_the_notebooks_folder(notebook_api, path):
    client, _ = notebook_api
    response = client.get("/api/v1/notebooks/content", params={"env": "prod", "path": path})
    assert response.status_code == 400


def test_save_strips_outputs_and_refuses_stale_writes(notebook_api):
    client, root = notebook_api
    url = "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb"
    document = client.get(url).json()
    cells = document["cells"]
    cells[2]["source"] = "x = 2"
    saved = client.put(url, json={"cells": cells, "expected_digest": document["digest"]})
    assert saved.status_code == 200
    on_disk = json.loads((root / "notebooks/market/trials.ipynb").read_text())
    assert all(cell.get("outputs", []) == [] for cell in on_disk["cells"])
    assert on_disk["cells"][1]["metadata"] == {
        "phlo": {"kind": "sql", "engine": "trino", "name": "trials"}
    }
    assert on_disk["cells"][2]["source"] == ["x = 2"]
    stale = client.put(url, json={"cells": cells, "expected_digest": document["digest"]})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "notebook_changed"


def test_creating_a_notebook_requires_that_it_does_not_exist(notebook_api):
    client, root = notebook_api
    url = "/api/v1/notebooks/content?env=prod&path=new/scratch.ipynb"
    created = client.put(url, json={"cells": [{"id": "a", "kind": "python", "source": "1"}]})
    assert created.status_code == 200
    assert (root / "notebooks/new/scratch.ipynb").is_file()
    assert client.put(url, json={"cells": []}).status_code == 409


def test_kernels_are_off_unless_the_operator_turns_them_on(notebook_api):
    client, _ = notebook_api
    response = client.post(
        "/api/v1/notebooks/kernels?env=prod", json={"path": "market/trials.ipynb"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "notebook_kernels_disabled"


def test_kernel_environment_drops_api_secrets(monkeypatch):
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    env = kernel_environment()
    assert "PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert not any("secret" == value for value in env.values())


def test_notebook_file_round_trip_keeps_jupyter_compatible_shape():
    cells = [
        notebook_files.NotebookCell(id="a", kind="markdown", source="# Hi\nthere"),
        notebook_files.NotebookCell(
            id="b", kind="sql", source="SELECT 1", engine="duckdb", name="t"
        ),
    ]
    rendered = json.loads(notebook_files.render_notebook(cells))
    assert rendered["nbformat"] == 4
    assert rendered["metadata"]["kernelspec"]["name"] == "python3"
    assert rendered["cells"][1]["metadata"]["phlo"] == {
        "kind": "sql",
        "engine": "duckdb",
        "name": "t",
    }
    with pytest.raises(notebook_files.NotebookInvalid):
        notebook_files.render_notebook(
            [notebook_files.NotebookCell(id="c", kind="python", source="", name="not valid")]
        )


@pytest.fixture
def kernel_api(notebook_api, monkeypatch):
    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    monkeypatch.setenv("PHLO_NOTEBOOK_KERNELS", "1")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_PROCESS", "1")
    registry = KernelRegistry()
    monkeypatch.setattr(v1_notebooks, "REGISTRY", registry)
    v1_query._QUERY_SESSIONS.clear()
    client, root = notebook_api
    yield client, root, registry
    for session in list(registry._kernels.values()):
        client.delete(f"/api/v1/notebooks/kernels/{session.id}?env=prod")
    v1_query._QUERY_SESSIONS.clear()


def _completed_query(query_id: str, actor: str) -> None:
    session = v1_query.QuerySession(
        query_id=query_id,
        actor=actor,
        env="prod",
        nessie_ref="main",
        sql="SELECT modality, starts FROM t",
        catalog="lake",
        row_limit=100,
    )
    session.status = "completed"
    session.updated_at = datetime.now(UTC)
    session.result = {
        "columns": [{"name": "modality", "type": "varchar"}, {"name": "starts", "type": "bigint"}],
        "rows": [{"modality": "CAR-T", "starts": 96}, {"modality": "TIL", "starts": 27}],
        "has_more": False,
    }
    v1_query._QUERY_SESSIONS[query_id] = session


@pytest.mark.timeout(180)
def test_kernel_runs_python_binds_query_results_and_queries_frames(kernel_api, actor):
    client, _, _ = kernel_api
    started = client.post(
        "/api/v1/notebooks/kernels?env=prod", json={"path": "market/trials.ipynb"}
    )
    assert started.status_code == 201, started.text
    kernel = started.json()["id"]
    base = f"/api/v1/notebooks/kernels/{kernel}"

    _completed_query("q-1", "analyst")
    bound = client.post(f"{base}/bind?env=prod", json={"query_id": "q-1", "name": "trials"}).json()
    assert bound["status"] == "ok"
    assert bound["table"]["row_count"] == 2

    run = client.post(
        f"{base}/execute?env=prod",
        json={
            "code": "growth = trials[trials.starts > 50]\nprint('rows', len(growth))",
            "name": "growth",
        },
    ).json()
    assert run["status"] == "ok"
    assert run["execution_count"] == 1
    assert any(o["type"] == "stream" and "rows 1" in o["text"] for o in run["outputs"])
    assert run["table"]["rows"] == [{"modality": "CAR-T", "starts": 96}]

    sql = client.post(
        f"{base}/sql?env=prod",
        json={"name": "total", "sql": "SELECT sum(starts) AS starts FROM trials"},
    ).json()
    assert sql["status"] == "ok"
    assert sql["table"]["rows"][0]["starts"] == 123

    failed = client.post(f"{base}/execute?env=prod", json={"code": "1/0"}).json()
    assert failed["status"] == "error"
    assert failed["outputs"][-1]["ename"] == "ZeroDivisionError"

    names = [v["name"] for v in client.get(f"{base}/variables?env=prod").json()["items"]]
    assert names == ["trials", "growth", "total"]

    secret = client.post(
        f"{base}/execute?env=prod",
        json={"code": "import os; print(os.environ.get('PHLO_PROJECT_PATH'))"},
    ).json()
    assert secret["outputs"][0]["text"].strip() == "None"

    actor["subject"] = "someone-else"
    assert client.get(f"{base}/variables?env=prod").status_code == 404
    assert client.post(f"{base}/execute?env=prod", json={"code": "1"}).status_code == 404
    actor["subject"] = "analyst"

    assert client.post(f"{base}/restart?env=prod").status_code == 200
    assert client.get(f"{base}/variables?env=prod").json()["items"] == []
    assert client.delete(f"{base}?env=prod").status_code == 204
    assert client.get(f"{base}/variables?env=prod").status_code == 404


@pytest.mark.timeout(180)
def test_bind_refuses_results_that_belong_to_someone_else(kernel_api):
    client, _, _ = kernel_api
    kernel = client.post(
        "/api/v1/notebooks/kernels?env=prod", json={"path": "market/trials.ipynb"}
    ).json()["id"]
    _completed_query("q-other", "bob")
    response = client.post(
        f"/api/v1/notebooks/kernels/{kernel}/bind?env=prod",
        json={"query_id": "q-other", "name": "trials"},
    )
    assert response.status_code == 404


def test_concurrent_saves_of_one_version_let_only_one_win(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    cells = [notebook_files.NotebookCell(id="a", kind="python", source="1")]
    first = notebook_files.write_notebook(tmp_path, "race.ipynb", cells, None)

    def save(n: int) -> str:
        edit = [notebook_files.NotebookCell(id="a", kind="python", source=str(n))]
        try:
            notebook_files.write_notebook(tmp_path, "race.ipynb", edit, first.digest)
        except notebook_files.NotebookConflict:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(save, range(8)))
    assert outcomes.count("saved") == 1
