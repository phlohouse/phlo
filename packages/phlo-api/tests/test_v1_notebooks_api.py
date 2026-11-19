"""HTTP contracts for Observatory notebooks: files, kernels, runs, schedules and publishing."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal
from phlo_api import notebook_files, notebook_promote, notebook_state
from phlo_api.api import v1_notebooks
from phlo_api.errors import PhloApiError, error_envelope
from phlo_api.notebook_kernels import (
    KernelRegistry,
    PackagesInvalid,
    kernel_environment,
    normalize_packages,
)
from phlo_api.notebook_runner import NotebookScheduler

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
def denied() -> set[str]:
    return set()


@pytest.fixture
def notebook_api(monkeypatch: pytest.MonkeyPatch, tmp_path, actor, denied):
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
    monkeypatch.setattr(
        v1_notebooks, "is_allowed", lambda _request, _env, action, _type: action not in denied
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


# -- file management, settings and access ---------------------------------------


def _open(client, path="market/trials.ipynb") -> dict:
    return client.get("/api/v1/notebooks/content", params={"env": "prod", "path": path}).json()


def test_access_reports_what_the_caller_may_do(notebook_api, denied):
    client, _ = notebook_api
    assert _open(client)["access"] == {"edit": True, "run": True, "schedule": True, "publish": True}
    denied.update({"object.write", "run.manage"})
    assert _open(client)["access"] == {
        "edit": False,
        "run": True,
        "schedule": False,
        "publish": True,
    }


def test_packages_are_saved_normalised_and_bad_requirements_refused(notebook_api):
    client, root = notebook_api
    document = _open(client)
    url = "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb"
    saved = client.put(
        url,
        json={
            "cells": document["cells"],
            "expected_digest": document["digest"],
            "packages": ["scikit-learn>=1.4", "Polars", "polars==1.0"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["packages"] == ["polars==1.0", "scikit-learn>=1.4"]
    on_disk = json.loads((root / "notebooks/market/trials.ipynb").read_text())
    assert on_disk["metadata"]["phlo"]["packages"] == ["polars==1.0", "scikit-learn>=1.4"]
    for bad in ["git+https://x/y.git", "-e .", "pkg @ https://x/p.whl", "not a req!"]:
        refused = client.put(
            url,
            json={
                "cells": document["cells"],
                "expected_digest": saved.json()["digest"],
                "packages": [bad],
            },
        )
        assert refused.status_code == 422, bad


@pytest.mark.parametrize(
    "packages",
    [["pkg @ file:///etc"], ["--index-url=http://x"], ["x"] * 51],
)
def test_normalize_packages_refuses_anything_but_names_and_versions(packages):
    with pytest.raises(PackagesInvalid):
        normalize_packages(packages)


def test_schedule_has_its_own_permission_and_survives_saves(notebook_api, actor, denied):
    client, root = notebook_api
    document = _open(client)
    url = "/api/v1/notebooks/schedule?env=prod&path=market/trials.ipynb"
    bad = client.put(url, json={"cron": "every day", "expected_digest": document["digest"]})
    assert bad.status_code == 422
    scheduled = client.put(
        url, json={"cron": "30 6 * * 1-5", "expected_digest": document["digest"]}
    ).json()
    assert scheduled["schedule"] == {
        "cron": "30 6 * * 1-5",
        "env": "prod",
        "owner": "analyst",
        "enabled": True,
    }
    actor["subject"] = "editor"
    resaved = client.put(
        "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb",
        json={"cells": scheduled["cells"], "expected_digest": scheduled["digest"]},
    ).json()
    assert resaved["schedule"]["owner"] == "analyst"
    cleared = client.put(url, json={"cron": None, "expected_digest": resaved["digest"]}).json()
    assert cleared["schedule"] is None
    assert (
        "phlo" not in json.loads((root / "notebooks/market/trials.ipynb").read_text())["metadata"]
    )


def test_move_and_delete_need_the_current_version_and_carry_outputs(notebook_api):
    client, root = notebook_api
    document = _open(client)
    notebook_state.record_output(
        root,
        "market/trials.ipynb",
        "py1",
        source_sha256="abc",
        actor="analyst",
        run={
            "status": "ok",
            "execution_count": 1,
            "duration_ms": 3,
            "outputs": [],
            "truncated": False,
            "table": None,
        },
        retention_days=14,
    )
    move = "/api/v1/notebooks/move?env=prod&path=market/trials.ipynb"
    stale = client.post(move, json={"to": "x/y.ipynb", "expected_digest": "0" * 64})
    assert stale.status_code == 409
    outside = client.post(move, json={"to": "../y.ipynb", "expected_digest": document["digest"]})
    assert outside.status_code == 400
    moved = client.post(
        move, json={"to": "research/trials-2026.ipynb", "expected_digest": document["digest"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["path"] == "research/trials-2026.ipynb"
    assert not (root / "notebooks/market").exists()  # empty folders are tidied away
    outputs = client.get(
        "/api/v1/notebooks/outputs?env=prod&path=research/trials-2026.ipynb"
    ).json()
    assert [item["cell_id"] for item in outputs["items"]] == ["py1"]

    client.put(
        "/api/v1/notebooks/content?env=prod&path=research/other.ipynb",
        json={"cells": []},
    )
    taken = client.post(
        "/api/v1/notebooks/move?env=prod&path=research/trials-2026.ipynb",
        json={"to": "research/other.ipynb", "expected_digest": moved.json()["digest"]},
    )
    assert taken.status_code == 409
    assert (root / "notebooks/research/other.ipynb").read_text().count('"cells": []') == 1

    delete = "/api/v1/notebooks/content"
    params = {"env": "prod", "path": "research/trials-2026.ipynb"}
    assert client.delete(delete, params={**params, "digest": "0" * 64}).status_code == 409
    gone = client.delete(delete, params={**params, "digest": moved.json()["digest"]})
    assert gone.status_code == 204
    assert not (root / "notebooks/research/trials-2026.ipynb").exists()
    assert client.get("/api/v1/notebooks/outputs", params=params).json()["items"] == []


def test_outputs_expire_and_stay_within_their_size_budget(tmp_path, monkeypatch):
    run = {"status": "ok", "outputs": [], "duration_ms": 1, "truncated": False}
    notebook_state.record_output(
        tmp_path, "a.ipynb", "old", source_sha256="s", actor="a", run=run, retention_days=1
    )
    later = time.time() + 2 * 86400
    monkeypatch.setattr(notebook_state, "_now", lambda: later)
    notebook_state.record_output(
        tmp_path, "a.ipynb", "new", source_sha256="s", actor="a", run=run, retention_days=1
    )
    assert list(notebook_state.read_outputs(tmp_path, "a.ipynb", 1)) == ["new"]
    monkeypatch.setattr(notebook_state, "MAX_OUTPUT_BYTES", 600)
    big = {**run, "outputs": [{"type": "stream", "text": "x" * 400}]}
    for cell in ("c1", "c2", "c3"):
        notebook_state.record_output(
            tmp_path, "a.ipynb", cell, source_sha256="s", actor="a", run=big, retention_days=1
        )
    assert list(notebook_state.read_outputs(tmp_path, "a.ipynb", 1)) == ["c3"]


# -- kernels and runs -----------------------------------------------------------

ROWS = {
    "columns": [{"name": "modality", "type": "varchar"}, {"name": "starts", "type": "bigint"}],
    "rows": [{"modality": "CAR-T", "starts": 96}, {"modality": "TIL", "starts": 27}],
    "has_more": False,
}


@pytest.fixture
def kernel_dir():
    # IPC socket paths must stay short, so not under pytest's long tmp_path.
    folder = Path(tempfile.mkdtemp(prefix="pk-"))
    folder.chmod(0o700)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def kernel_api(notebook_api, monkeypatch, kernel_dir):
    pytest.importorskip("jupyter_client")
    pytest.importorskip("ipykernel")
    monkeypatch.setenv("PHLO_NOTEBOOK_KERNELS", "1")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_NOTEBOOK_KERNEL_DIR", str(kernel_dir))
    registry = KernelRegistry(kernel_dir)
    monkeypatch.setattr(v1_notebooks, "REGISTRY", registry)
    monkeypatch.setattr(v1_notebooks, "preview_catalog", lambda env, ref: "lake")
    queries: list[str] = []

    def fake_trino(_deployment):
        async def run(env, sql, cancelled):
            queries.append(sql)
            return ROWS

        return run

    monkeypatch.setattr(v1_notebooks, "trino_rows", fake_trino)
    client, root = notebook_api
    yield client, root, registry, queries
    for record in registry.records():
        registry._stop_process(record)
        registry._remove(record)


def _start(client, path="market/trials.ipynb") -> dict:
    started = client.post("/api/v1/notebooks/kernels?env=prod", json={"path": path})
    assert started.status_code == 201, started.text
    kernel = started.json()
    deadline = time.monotonic() + 600
    while kernel["status"] in {"installing", "starting"} and time.monotonic() < deadline:
        time.sleep(0.5)
        kernel = next(
            k
            for k in client.get("/api/v1/notebooks/kernels?env=prod").json()
            if k["id"] == kernel["id"]
        )
    assert kernel["status"] == "idle", kernel
    return kernel


def _run(client, kernel: str, body: dict, *, seen: list | None = None) -> dict:
    base = f"/api/v1/notebooks/kernels/{kernel}/runs"
    started = client.post(f"{base}?env=prod", json=body)
    assert started.status_code == 202, started.text
    run = started.json()
    deadline = time.monotonic() + 120
    while run["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        run = client.get(f"{base}/{run['id']}?env=prod").json()
        if seen is not None and run["status"] == "running" and run["result"]:
            seen.append(run["result"])
    return run


@pytest.mark.timeout(240)
def test_cells_run_in_the_background_and_keep_their_outputs(kernel_api, actor, denied):
    client, _, _, queries = kernel_api
    kernel = _start(client)["id"]
    base = f"/api/v1/notebooks/kernels/{kernel}"

    trials = _run(
        client,
        kernel,
        {
            "kind": "trino",
            "source": "SELECT modality, starts FROM lake.gold.trials",
            "name": "trials",
            "cell_id": "q1",
        },
    )
    assert trials["status"] == "ok", trials
    assert trials["result"]["table"]["row_count"] == 2
    assert queries == ["SELECT modality, starts FROM lake.gold.trials"]

    invalid = client.post(
        f"{base}/runs?env=prod",
        json={"kind": "trino", "source": "DELETE FROM lake.gold.trials", "name": "x"},
    )
    assert invalid.status_code == 422

    seen: list = []
    slow = _run(
        client,
        kernel,
        {
            "kind": "python",
            "source": "import time\nfor i in range(4):\n    print('tick', i, flush=True)\n    time.sleep(0.4)\ngrowth = trials[trials.starts > 50]",
            "name": "growth",
            "cell_id": "py1",
        },
        seen=seen,
    )
    assert slow["status"] == "ok"
    assert slow["result"]["execution_count"] == 1
    assert slow["result"]["table"]["rows"] == [{"modality": "CAR-T", "starts": 96}]
    assert any("tick 0" in "".join(o["text"] or "" for o in r["outputs"]) for r in seen)

    total = _run(
        client,
        kernel,
        {"kind": "duckdb", "source": "SELECT sum(starts) AS starts FROM trials", "name": "total"},
    )
    assert total["result"]["table"]["rows"][0]["starts"] == 123

    failed = _run(client, kernel, {"kind": "python", "source": "1/0"})
    assert failed["status"] == "error"
    assert failed["result"]["outputs"][-1]["ename"] == "ZeroDivisionError"

    names = [v["name"] for v in client.get(f"{base}/variables?env=prod").json()["items"]]
    assert names == ["trials", "i", "growth", "total"]

    completions = client.post(
        f"{base}/complete?env=prod", json={"code": "growth.sort_v", "cursor": 13}
    ).json()
    assert "sort_values" in " ".join(completions["matches"])

    secret = _run(
        client,
        kernel,
        {"kind": "python", "source": "import os; print(os.environ.get('PHLO_PROJECT_PATH'))"},
    )
    assert secret["result"]["outputs"][0]["text"].strip() == "None"

    saved = client.get("/api/v1/notebooks/outputs?env=prod&path=market/trials.ipynb").json()
    by_cell = {item["cell_id"]: item for item in saved["items"]}
    assert set(by_cell) == {"q1", "py1"}
    assert by_cell["q1"]["run"]["table"]["row_count"] == 2
    assert by_cell["q1"]["actor"] == "analyst"

    denied.add("dataset.query")
    forbidden = client.post(
        f"{base}/runs?env=prod",
        json={"kind": "trino", "source": "SELECT 1", "name": "one"},
    )
    assert forbidden.status_code == 403
    denied.clear()

    actor["subject"] = "someone-else"
    assert client.get(f"{base}/variables?env=prod").status_code == 404
    assert (
        client.post(f"{base}/runs?env=prod", json={"kind": "python", "source": "1"}).status_code
        == 404
    )
    actor["subject"] = "analyst"

    assert client.post(f"{base}/restart?env=prod").status_code == 200
    assert client.get(f"{base}/variables?env=prod").json()["items"] == []
    assert client.delete(f"{base}?env=prod").status_code == 204
    assert client.get(f"{base}/variables?env=prod").status_code == 404


@pytest.mark.timeout(240)
def test_interrupt_stops_a_long_cell_and_the_kernel_reports_busy(kernel_api):
    client, _, _, _ = kernel_api
    kernel = _start(client)["id"]
    base = f"/api/v1/notebooks/kernels/{kernel}"
    started = client.post(
        f"{base}/runs?env=prod",
        json={"kind": "python", "source": "import time\ntime.sleep(60)"},
    ).json()
    time.sleep(0.5)
    assert [k["status"] for k in client.get("/api/v1/notebooks/kernels?env=prod").json()] == [
        "busy"
    ]
    second = client.post(f"{base}/runs?env=prod", json={"kind": "python", "source": "1"})
    assert second.status_code == 409
    assert client.post(f"{base}/interrupt?env=prod").status_code == 200
    deadline = time.monotonic() + 30
    run = started
    while run["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
        run = client.get(f"{base}/runs/{started['id']}?env=prod").json()
    assert run["status"] == "cancelled"


def _wheel(folder: Path) -> Path:
    """A tiny pure-Python wheel, so installing packages needs no network."""
    wheel = folder / "phlo_nb_demo-1.0-py3-none-any.whl"
    info = "phlo_nb_demo-1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("phlo_nb_demo.py", "VALUE = 42\n")
        archive.writestr(
            f"{info}/METADATA", "Metadata-Version: 2.1\nName: phlo-nb-demo\nVersion: 1.0\n"
        )
        archive.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{info}/RECORD", "")
    return wheel


@pytest.fixture
def package_index(tmp_path):
    root = tmp_path / "index"
    project = root / "simple" / "phlo-nb-demo"
    project.mkdir(parents=True)
    wheel = _wheel(project)
    (project / "index.html").write_text(f'<a href="{wheel.name}">{wheel.name}</a>')
    (root / "simple" / "index.html").write_text('<a href="phlo-nb-demo/">phlo-nb-demo</a>')
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/simple"
    server.shutdown()
    server.server_close()


@pytest.mark.timeout(600)
def test_notebook_packages_install_into_their_own_environment(
    kernel_api, monkeypatch, package_index
):
    client, _, registry, _ = kernel_api
    document = _open(client)
    url = "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb"
    client.put(
        url,
        json={
            "cells": document["cells"],
            "expected_digest": document["digest"],
            "packages": ["phlo-nb-demo==1.0"],
        },
    )
    refused = client.post(
        "/api/v1/notebooks/kernels?env=prod", json={"path": "market/trials.ipynb"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "notebook_packages_disabled"

    monkeypatch.setenv("PHLO_NOTEBOOK_PACKAGES", "1")
    monkeypatch.setenv("PHLO_NOTEBOOK_PACKAGE_INDEX", package_index)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    first = client.post("/api/v1/notebooks/kernels?env=prod", json={"path": "market/trials.ipynb"})
    assert first.json()["status"] == "installing"
    kernel = _start(client)["id"]
    run = _run(
        client,
        kernel,
        {"kind": "python", "source": "import phlo_nb_demo\nprint(phlo_nb_demo.VALUE)"},
    )
    assert run["result"]["outputs"][0]["text"].strip() == "42", run
    packages = client.get(f"/api/v1/notebooks/kernels/{kernel}/packages?env=prod").json()
    sources = {item["name"].lower(): item["source"] for item in packages["items"]}
    assert packages["requested"] == ["phlo-nb-demo==1.0"]
    assert sources["phlo-nb-demo"] == "notebook"
    assert sources["ipykernel"] == "base"


@pytest.mark.timeout(300)
def test_run_all_in_background_and_on_a_schedule(kernel_api, monkeypatch, kernel_dir):
    client, root, registry, queries = kernel_api
    started = client.post("/api/v1/notebooks/runs?env=prod&path=market/trials.ipynb")
    assert started.status_code == 202
    deadline = time.monotonic() + 120
    runs = {}
    while time.monotonic() < deadline:
        runs = client.get("/api/v1/notebooks/runs?env=prod&path=market/trials.ipynb").json()
        if runs["items"] and runs["items"][0]["status"] != "running":
            break
        time.sleep(0.3)
    latest = runs["items"][0]
    assert latest["status"] == "ok", latest
    assert latest["trigger"] == "manual"
    assert [c["cell_id"] for c in latest["cells"]] == ["q1", "py1"]
    assert registry.records() == []  # the run's own kernel is stopped afterwards
    outputs = client.get("/api/v1/notebooks/outputs?env=prod&path=market/trials.ipynb").json()
    assert {item["cell_id"] for item in outputs["items"]} == {"q1", "py1"}

    pytest.importorskip("croniter")
    document = _open(client)
    client.put(
        "/api/v1/notebooks/schedule?env=prod&path=market/trials.ipynb",
        json={"cron": "*/15 * * * *", "expected_digest": document["digest"]},
    )

    async def trino(env, sql, cancelled):
        return ROWS

    scheduler = NotebookScheduler(
        registry,
        project=lambda: root,
        trino=trino,
        timeout=60,
        output_days=14,
        allow_packages=False,
        envs=lambda: {"prod"},
    )
    import asyncio

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    now = now - timedelta(minutes=now.minute % 15) + timedelta(minutes=2)
    assert asyncio.run(scheduler.tick(now)) == ["market/trials.ipynb"]
    assert asyncio.run(scheduler.tick(now)) == []  # once per due time
    scheduled = client.get("/api/v1/notebooks/runs?env=prod&path=market/trials.ipynb").json()
    assert scheduled["items"][0]["trigger"] == "schedule"
    assert scheduled["items"][0]["actor"] == "analyst"


# -- publishing -----------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def git_project(notebook_api, monkeypatch):
    if shutil.which("git") is None:
        # reason: the git commit routes shell out to git, which minimal images may lack.
        pytest.skip("git is not installed")
    client, root = notebook_api
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Phlo API")
    _git(root, "config", "user.email", "api@phlo.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.setenv("PHLO_NOTEBOOK_GIT_COMMIT", "1")
    return client, root


def test_git_commit_only_the_chosen_notebook_and_never_on_main(git_project):
    client, root = git_project
    document = _open(client)
    cells = document["cells"]
    cells[2]["source"] = "x = 3"
    client.put(
        "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb",
        json={"cells": cells, "expected_digest": document["digest"]},
    )
    (root / "unrelated.txt").write_text("staged elsewhere")
    _git(root, "add", "unrelated.txt")

    state = client.get("/api/v1/notebooks/git?env=prod").json()
    assert state["branch"] == "main" and state["protected"] is True
    assert state["changed"] == ["notebooks/market/trials.ipynb"]
    body = {"paths": ["notebooks/market/trials.ipynb"], "message": "Tighten trial filter"}
    refused = client.post("/api/v1/notebooks/git/commit?env=prod", json=body)
    assert refused.status_code == 409

    _git(root, "checkout", "-q", "-b", "analysis")
    outside = client.post(
        "/api/v1/notebooks/git/commit?env=prod",
        json={"paths": ["unrelated.txt"], "message": "x"},
    )
    assert outside.status_code == 409
    committed = client.post("/api/v1/notebooks/git/commit?env=prod", json=body)
    assert committed.status_code == 200, committed.text
    assert committed.json()["branch"] == "analysis"
    files_in_commit = _git(root, "show", "--name-only", "--format=%an|%s", "HEAD").split()
    assert files_in_commit[0] == "analyst|Tighten"
    assert "notebooks/market/trials.ipynb" in files_in_commit
    assert "unrelated.txt" not in files_in_commit
    assert "unrelated.txt" in _git(root, "diff", "--cached", "--name-only")


def test_git_commit_is_off_unless_the_operator_allows_it(notebook_api):
    client, _ = notebook_api
    state = client.get("/api/v1/notebooks/git?env=prod").json()
    assert state["enabled"] is False
    response = client.post(
        "/api/v1/notebooks/git/commit?env=prod",
        json={"paths": ["notebooks/market/trials.ipynb"], "message": "x"},
    )
    assert response.status_code == 503


def test_promote_turns_a_sql_cell_into_a_dbt_model_with_lineage(notebook_api, monkeypatch, denied):
    client, root = notebook_api
    monkeypatch.setattr(v1_notebooks, "preview_catalog", lambda env, ref: "lake")
    dbt = root / "workflows/transforms/dbt"
    (dbt / "models/sources").mkdir(parents=True)
    (dbt / "dbt_project.yml").write_text("name: demo\n")
    (dbt / "models/stg_trials.sql").write_text("select 1")
    (dbt / "models/sources/raw.yml").write_text(
        "version: 2\nsources:\n  - name: raw\n    schema: bronze\n    tables:\n      - name: orders\n"
    )
    document = _open(client)
    cells = document["cells"]
    cells[1]["source"] = (
        "SELECT t.modality, count(*) AS n FROM lake.silver.stg_trials AS t "
        "JOIN lake.bronze.orders o ON o.id = t.id JOIN gold.registry r ON r.id = t.id GROUP BY 1"
    )
    client.put(
        "/api/v1/notebooks/content?env=prod&path=market/trials.ipynb",
        json={"cells": cells, "expected_digest": document["digest"]},
    )
    url = "/api/v1/notebooks/promote?env=prod&path=market/trials.ipynb"
    preview = client.post(url, json={"cell_id": "q1", "model": "trial_modalities"}).json()
    sql = preview["files"][0]
    assert sql["path"] == "workflows/transforms/dbt/models/notebooks/trial_modalities.sql"
    assert "{{ ref('stg_trials') }} AS t" in sql["content"]
    assert "{{ source('raw', 'orders') }} AS o" in sql["content"]
    assert "gold.registry AS r" in sql["content"]
    assert "lake." not in sql["content"]
    assert not (root / sql["path"]).exists()

    written = client.post(url, json={"cell_id": "q1", "model": "trial_modalities", "apply": True})
    assert written.json()["applied"] is True
    assert (root / sql["path"]).read_text() == sql["content"]
    again = client.post(url, json={"cell_id": "q1", "model": "trial_modalities", "apply": True})
    assert again.status_code == 409
    clash = client.post(url, json={"cell_id": "q1", "model": "stg_trials"})
    assert clash.status_code == 409
    python = client.post(url, json={"cell_id": "py1", "model": "nope"})
    assert python.status_code == 422


def test_promote_refuses_projects_without_dbt(tmp_path):
    with pytest.raises(notebook_promote.PromoteInvalid):
        notebook_promote.plan(
            tmp_path, notebook="a.ipynb", cell_name=None, sql="SELECT 1", model="m", catalog="lake"
        )
