"""Focused tests for the release artifact golden-path harness."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "release_golden_path", REPO_ROOT / "scripts" / "release_golden_path.py"
)
assert _spec and _spec.loader
release_golden_path = importlib.util.module_from_spec(_spec)
sys.modules["release_golden_path"] = release_golden_path
_spec.loader.exec_module(release_golden_path)


def _config(tmp_path: Path) -> release_golden_path.RunConfig:
    project = tmp_path / "project"
    return release_golden_path.RunConfig(
        repo_root=tmp_path,
        project_dir=project,
        wheelhouse=tmp_path / "wheelhouse",
        operator_env=tmp_path / "operator-env",
        project_name="phlo-qa001-test",
    )


def test_compose_commands_are_project_scoped(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert release_golden_path.compose_command(config, "up", "--detach") == [
        "docker",
        "compose",
        "-p",
        "phlo-qa001-test",
        "--file",
        str(config.compose_file),
        "--env-file",
        str(config.project_dir / ".phlo" / ".env"),
        "--env-file",
        str(config.project_dir / ".phlo" / ".env.local"),
        "up",
        "--detach",
    ]


def test_project_names_are_unique() -> None:
    first = release_golden_path.project_name()
    second = release_golden_path.project_name()

    assert first.startswith("phlo-qa001-")
    assert second.startswith("phlo-qa001-")
    assert first != second


def test_virtualenv_executables_use_windows_scripts_directory(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    operator_scripts = config.operator_env / "Scripts"
    project_scripts = config.project_env / "Scripts"
    operator_scripts.mkdir(parents=True)
    project_scripts.mkdir(parents=True)
    operator_python = operator_scripts / "python.exe"
    operator_bin = operator_scripts / "phlo.exe"
    project_python = project_scripts / "python.exe"
    for executable in (operator_python, operator_bin, project_python):
        executable.touch()

    monkeypatch.setattr(release_golden_path.os, "name", "nt")

    assert config.operator_python == operator_python
    assert config.operator_bin == operator_bin
    assert config.project_python == project_python


def test_virtualenv_executables_use_unix_bin_directory(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    operator_bin = config.operator_env / "bin" / "phlo"
    project_python = config.project_env / "bin" / "python"
    operator_bin.parent.mkdir(parents=True)
    project_python.parent.mkdir(parents=True)
    operator_bin.touch()
    project_python.touch()

    monkeypatch.setattr(release_golden_path.os, "name", "posix")

    assert config.operator_bin == operator_bin
    assert config.project_python == project_python


def test_operator_install_uses_wheelhouse_and_not_editable_source(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(release_golden_path, "run", lambda args, **_: commands.append(args))

    release_golden_path.install_operator(config)

    assert any(command[0:3] == ["uv", "pip", "install"] for command in commands)
    local_install = commands[-1]
    assert "--no-index" in local_install
    assert "--no-deps" in local_install
    assert "--reinstall" in local_install
    assert "--find-links" in local_install
    assert str(config.wheelhouse) in local_install
    assert "-e" not in local_install
    assert "." not in local_install
    assert local_install[-5:] == ["phlo", "phlo-api", "phlo-dbt", "phlo-dlt", "phlo-pandera"]
    assert "phlo[core-services]" in commands[-2]
    assert "phlo-api" in commands[-2]
    assert "phlo-dbt" in commands[-2]


def test_transform_fixture_has_a_raw_events_mart(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.project_dir.mkdir()

    release_golden_path.write_transform_fixture(config)

    dbt_dir = config.project_dir / "workflows" / "transforms" / "dbt"
    assert "profile: phlo" in (dbt_dir / "dbt_project.yml").read_text(encoding="utf-8")
    assert "name: events" in (dbt_dir / "models" / "sources" / "raw.yml").read_text(
        encoding="utf-8"
    )
    assert "source('raw', 'events')" in (
        dbt_dir / "models" / "marts" / "events_mart.sql"
    ).read_text(encoding="utf-8")
    assert "schema='marts'" in (dbt_dir / "models" / "marts" / "events_mart.sql").read_text(
        encoding="utf-8"
    )


def test_wap_fixture_rejects_one_run_and_preserves_the_happy_path(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    fixture_dir = config.project_dir / "workflows" / "ingestion" / "csv"
    fixture_dir.mkdir(parents=True)

    release_golden_path.write_wap_fixture(config, "rejected-run")

    fixture = (fixture_dir / "release_wap_check.py").read_text(encoding="utf-8")
    assert '@dg.asset_check(asset="dlt_events")' in fixture
    assert "blocking=True" not in fixture
    assert "run_tags = context.run.tags or {}" in fixture
    assert 'run_tags.get("phlo/run_id") == "rejected-run"' in fixture
    assert "passed=not rejected" in fixture
    assert "intentional_quality_rejection" in fixture

    fake_dagster = ModuleType("dagster")
    fake_dagster.asset_check = lambda **_: lambda check: check
    fake_dagster.AssetCheckResult = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "dagster", fake_dagster)
    namespace: dict[str, object] = {}
    exec(compile(fixture, str(fixture_dir / "release_wap_check.py"), "exec"), namespace)
    check = namespace["release_golden_path_wap_check"]

    assert callable(check)
    assert check(SimpleNamespace(run=SimpleNamespace(tags={}))) == {
        "passed": True,
        "metadata": {"reason": "happy_path"},
    }
    assert check(SimpleNamespace(run=SimpleNamespace(tags={"phlo/run_id": "rejected-run"}))) == {
        "passed": False,
        "metadata": {"reason": "intentional_quality_rejection"},
    }


def test_report_policy_fixture_relies_on_the_service_token_scope(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    release_golden_path.write_report_policy_fixture(config)

    policy = (config.project_dir / ".phlo" / "authorization" / "policies.yaml").read_text(
        encoding="utf-8"
    )
    assert "qa001_role: report_reader" in policy
    assert "authentication_source: service_token" in policy
    assert policy.count("action: run.read") == 2
    assert "action: catalog.read" in policy
    assert "action: run.execute" in policy
    assert 'action: "*"' not in policy
    report_policy = policy.split("  - policy_id: release-golden-path-report-read", 1)[1]
    assert "action: run.read" in report_policy
    assert 'id_pattern: "*"' in report_policy
    assert "action: catalog.read" not in report_policy
    assert "action: run.execute" not in report_policy


def test_main_configures_wap_before_launching_the_generated_run(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, str] = {}

    for name in (
        "build_wheelhouse",
        "install_operator",
        "create_project",
        "write_transform_fixture",
        "write_wap_fixture",
        "align_project_name",
        "install_project_dependencies",
        "configure_non_dev_compose",
        "start_stack",
        "materialize_partition",
        "verify_minio_storage",
        "materialize_transform",
        "verify_rows",
        "wait_for_wap_promotion",
        "verify_run_report",
        "verify_rejected_wap_report",
        "configure_wap",
    ):
        monkeypatch.setattr(release_golden_path, name, lambda *_args, **_kwargs: None)

    def configure(_config) -> None:
        captured["configured"] = "true"

    def materialize(_config) -> release_golden_path.WapRun:
        captured["wap"] = "generated"
        return release_golden_path.WapRun("generated", "dagster-run")

    monkeypatch.setattr(release_golden_path, "configure_non_dev_compose", configure)
    monkeypatch.setattr(release_golden_path, "materialize_wap", materialize)
    monkeypatch.setattr(release_golden_path, "project_name", lambda: "phlo-qa001-test")
    logical_run_ids = iter(("rejected-logical-run", "promoted-logical-run"))
    monkeypatch.setattr(
        release_golden_path.uuid,
        "uuid4",
        lambda: type("Id", (), {"hex": next(logical_run_ids)})(),
    )

    assert (
        release_golden_path.main(
            ["--repo-root", str(tmp_path), "--project-dir", str(tmp_path / "project")]
        )
        == 0
    )
    assert captured == {
        "configured": "true",
        "wap": "generated",
    }


def test_transform_materialization_preserves_the_partition(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(release_golden_path, "run", lambda args, **_: commands.append(args))

    release_golden_path.materialize_transform(config)

    assert commands == [
        [
            str(config.operator_bin),
            "materialize",
            "events_mart",
            "--partition",
            config.partition,
        ]
    ]


def test_service_url_uses_the_project_scoped_dynamic_port(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []

    def fake_run(args, **kwargs):
        commands.append(args)
        assert kwargs["capture_output"] is True
        return release_golden_path.subprocess.CompletedProcess(args, 0, "0.0.0.0:49123\n", "")

    monkeypatch.setattr(release_golden_path, "run", fake_run)

    assert release_golden_path.service_url(config, "dagster", 3000, "/graphql") == (
        "http://127.0.0.1:49123/graphql"
    )
    assert commands == [release_golden_path.compose_command(config, "port", "dagster", "3000")]


def test_wap_materialization_uses_live_selector_and_dynamic_urls(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    commands: list[tuple[list[str], dict]] = []
    urls = {
        ("dagster", 3000, "/graphql"): "http://127.0.0.1:3000/graphql",
        ("nessie", 19120, ""): "http://127.0.0.1:19120",
    }
    monkeypatch.setattr(
        release_golden_path,
        "service_url",
        lambda _config, service, port, path="": urls[(service, port, path)],
    )
    monkeypatch.setattr(
        release_golden_path, "discover_dagster_selector", lambda *_: ("location", "repository")
    )

    def fake_run(args, **kwargs):
        commands.append((args, kwargs))
        return release_golden_path.subprocess.CompletedProcess(
            args,
            0,
            "Launched WAP materialization for dlt_events on pipeline-run-logical (logical run logical, Dagster run dagster-1)\n",
            "",
        )

    monkeypatch.setattr(release_golden_path, "run", fake_run)
    values = iter(["token"])
    monkeypatch.setattr(
        release_golden_path.uuid, "uuid4", lambda: type("Id", (), {"hex": next(values)})()
    )

    wap_run = release_golden_path.materialize_wap(config)

    assert wap_run == release_golden_path.WapRun("logical", "dagster-1")
    args, kwargs = commands[0]
    assert args[-4:] == ["materialize", "dlt_events", "--partition", config.partition]
    assert "--wap" not in args
    assert kwargs["env"]["NESSIE_HOST"] == "127.0.0.1"
    assert kwargs["env"]["NESSIE_PORT"] == "19120"
    assert kwargs["env"]["PHLO_DAGSTER_ACCESS_TOKEN"].startswith("phlo-api:")


def test_configure_wap_writes_owned_dagster_endpoint_and_selector(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    config.project_dir.mkdir()
    config.project_dir.joinpath("phlo.yaml").write_text("name: test\n", encoding="utf-8")
    monkeypatch.setattr(release_golden_path, "service_token", lambda *_: "token")
    monkeypatch.setattr(
        release_golden_path, "discover_dagster_selector", lambda *_: ("location", "repository")
    )
    monkeypatch.setattr(
        release_golden_path,
        "service_url",
        lambda *_args: "http://127.0.0.1:3000/graphql",
    )

    release_golden_path.configure_wap(config)

    assert config.project_dir.joinpath("phlo.yaml").read_text(encoding="utf-8") == (
        "name: test\n\n"
        "wap:\n"
        "  enabled: true\n"
        "  job_name: __ASSET_JOB\n"
        "  repository_location_name: location\n"
        "  repository_name: repository\n"
        "  dagster_url: http://127.0.0.1:3000/graphql\n"
    )


def test_wap_wait_requires_success_then_promotion_tag(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        release_golden_path, "service_url", lambda *_: "http://127.0.0.1:3000/graphql"
    )
    payloads = iter(
        [
            {"data": {"pipelineRunOrError": {"status": "STARTED", "tags": []}}},
            {"data": {"pipelineRunOrError": {"status": "SUCCESS", "tags": []}}},
            {
                "data": {
                    "pipelineRunOrError": {
                        "status": "SUCCESS",
                        "tags": [{"key": "phlo/wap_promoted", "value": "true"}],
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(release_golden_path, "graphql", lambda *_: next(payloads))
    monkeypatch.setattr(release_golden_path.time, "sleep", lambda _: None)

    release_golden_path.wait_for_wap_promotion(
        config, release_golden_path.WapRun("logical", "dagster-1")
    )


def test_run_report_requires_the_wap_logical_run_id(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        release_golden_path,
        "service_url",
        lambda _config, _service, _port, path: f"http://127.0.0.1:4000{path}",
    )

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    requests = []

    def urlopen(request, **_kwargs):
        requests.append(request)
        if request.full_url.endswith("/runs/logical/attempts/1/report"):
            return Response(json.dumps({"run_id": "logical"}).encode())
        raise release_golden_path.urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            Response(
                json.dumps({"error": "forbidden", "reason": "run_report_scope_mismatch"}).encode()
            ),
        )

    monkeypatch.setattr(release_golden_path.urllib.request, "urlopen", urlopen)

    release_golden_path.verify_run_report(
        config, release_golden_path.WapRun("logical", "dagster-1")
    )

    assert requests[0].get_header("Authorization") == f"Bearer {config.report_token}"
    assert requests[1].get_header("Authorization") == f"Bearer {config.report_token}"


def test_rejected_wap_report_waits_for_rejection_projection(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    wap_run = release_golden_path.WapRun("rejected", "dagster-rejected")
    reports = iter(
        (
            {
                "run_id": "rejected",
                "quality": [{"blocking": True, "passed": False}],
                "catalog_changes": [],
            },
            {
                "run_id": "rejected",
                "quality": [{"blocking": True, "passed": False}],
                "catalog_changes": [{"merge_outcome": "rejected_quality"}],
            },
        )
    )
    monkeypatch.setattr(release_golden_path, "fetch_run_report", lambda *_: next(reports))
    monkeypatch.setattr(release_golden_path.time, "sleep", lambda _: None)
    monkeypatch.setattr(release_golden_path, "service_url", lambda *_: "http://dagster/graphql")
    monkeypatch.setattr(release_golden_path, "service_token", lambda *_: "service-token")
    monkeypatch.setattr(release_golden_path, "wap_service_secret", lambda _: "secret")
    monkeypatch.setattr(
        release_golden_path,
        "graphql",
        lambda *_: {"data": {"pipelineRunOrError": {"tags": []}}},
    )

    release_golden_path.verify_rejected_wap_report(config, wap_run)


def test_rejected_wap_report_requires_failed_quality_and_rejection_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    wap_run = release_golden_path.WapRun("rejected", "dagster-rejected")
    monkeypatch.setattr(
        release_golden_path,
        "fetch_run_report",
        lambda *_: {
            "run_id": "rejected",
            "quality": [{"blocking": True, "passed": False}],
            "catalog_changes": [{"merge_outcome": "rejected_quality"}],
        },
    )
    monkeypatch.setattr(release_golden_path, "service_url", lambda *_: "http://dagster/graphql")
    monkeypatch.setattr(release_golden_path, "service_token", lambda *_: "service-token")
    monkeypatch.setattr(release_golden_path, "wap_service_secret", lambda _: "secret")
    monkeypatch.setattr(
        release_golden_path,
        "graphql",
        lambda *_: {"data": {"pipelineRunOrError": {"tags": []}}},
    )

    release_golden_path.verify_rejected_wap_report(config, wap_run)


def test_rejected_wap_report_rejects_any_promotion(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    wap_run = release_golden_path.WapRun("rejected", "dagster-rejected")
    monkeypatch.setattr(
        release_golden_path,
        "fetch_run_report",
        lambda *_: {
            "run_id": "rejected",
            "quality": [{"blocking": True, "passed": False}],
            "catalog_changes": [{"merge_outcome": "rejected_quality"}],
        },
    )
    monkeypatch.setattr(release_golden_path, "service_url", lambda *_: "http://dagster/graphql")
    monkeypatch.setattr(release_golden_path, "service_token", lambda *_: "service-token")
    monkeypatch.setattr(release_golden_path, "wap_service_secret", lambda _: "secret")
    monkeypatch.setattr(
        release_golden_path,
        "graphql",
        lambda *_: {
            "data": {
                "pipelineRunOrError": {"tags": [{"key": "phlo/wap_promoted", "value": "true"}]}
            }
        },
    )

    try:
        release_golden_path.verify_rejected_wap_report(config, wap_run)
    except RuntimeError as exc:
        assert "was promoted" in str(exc)
    else:
        raise AssertionError("a rejected WAP run must not be promoted")


def test_existing_project_or_sibling_is_rejected_without_touching_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        release_golden_path, "build_wheelhouse", lambda _: (_ for _ in ()).throw(AssertionError)
    )
    for existing_name in ("project", "wheelhouse", "operator-env"):
        case = tmp_path / existing_name
        project = case / "project"
        existing = project if existing_name == "project" else case / existing_name
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("caller-owned\n", encoding="utf-8")

        assert (
            release_golden_path.main(["--repo-root", str(case), "--project-dir", str(project)]) == 2
        )
        assert marker.read_text(encoding="utf-8") == "caller-owned\n"


def test_verify_rows_requires_the_expected_fixture_count(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    result = release_golden_path.subprocess.CompletedProcess([], 0, stdout='"2"\n', stderr="")
    monkeypatch.setattr(release_golden_path, "run", lambda *args, **kwargs: result)

    release_golden_path.verify_rows(config)

    for output, expected in (
        ("\n", "no row count"),
        ("0\n", "does not match expected 2"),
        ("1\n", "does not match expected 2"),
        ("3\n", "does not match expected 2"),
        ("oops\n", "no row count"),
    ):
        invalid = release_golden_path.subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        monkeypatch.setattr(
            release_golden_path, "run", lambda *args, result=invalid, **kwargs: result
        )
        try:
            release_golden_path.verify_rows(config)
        except RuntimeError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"invalid raw.events result should fail: {output!r}")


def test_minio_storage_check_proves_readiness_and_an_owned_write(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(release_golden_path, "run", lambda args, **_: commands.append(args))
    monkeypatch.setattr(
        release_golden_path.uuid, "uuid4", lambda: type("Id", (), {"hex": "owned-bucket"})()
    )

    release_golden_path.verify_minio_storage(config)

    assert commands[0][:-1] == release_golden_path.compose_command(
        config, "exec", "--no-TTY", "minio", "/bin/sh", "-c"
    )
    check = commands[0][-1]
    assert "minio/health/ready" in check
    assert "mc mb --ignore-existing local/qa001-evidence-owned-bucket" in check
    assert "mc pipe local/qa001-evidence-owned-bucket/ready" in check
    assert "mc stat local/qa001-evidence-owned-bucket/ready" in check


def test_missing_raw_diagnostics_prints_recent_dagster_run_ids_and_statuses(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(release_golden_path, "service_url", lambda *_: "http://dagster/graphql")
    monkeypatch.setattr(release_golden_path, "service_token", lambda *_: "diagnostic-token")
    monkeypatch.setattr(release_golden_path, "wap_service_secret", lambda _: "secret")
    monkeypatch.setattr(
        release_golden_path,
        "graphql",
        lambda *_: {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [{"runId": "raw-ingest-run", "status": "FAILURE"}],
                }
            }
        },
    )

    release_golden_path.emit_missing_raw_diagnostics(config)

    output = capsys.readouterr().out
    assert '"runId": "raw-ingest-run"' in output
    assert '"status": "FAILURE"' in output


def test_verify_rows_reports_trino_query_output(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    error = release_golden_path.subprocess.CalledProcessError(
        1,
        ["trino"],
        output="Table 'events_mart' does not exist\n",
    )
    monkeypatch.setattr(
        release_golden_path,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    try:
        release_golden_path.verify_rows(config, table="raw_marts.events_mart")
    except RuntimeError as exc:
        assert (
            str(exc)
            == "Trino query failed for raw_marts.events_mart: Table 'events_mart' does not exist"
        )
    else:
        raise AssertionError("failed Trino query should report its output")


def test_start_stack_diagnoses_owned_compose_failure_in_order(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []
    startup_error = release_golden_path.subprocess.CalledProcessError(1, ["docker", "compose"])

    def fake_run(args, **kwargs):
        commands.append(args)
        if args[-3:] == ["up", "--detach", "--build"]:
            raise startup_error
        return release_golden_path.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(release_golden_path, "run", fake_run)

    try:
        release_golden_path.start_stack(config)
    except release_golden_path.subprocess.CalledProcessError as exc:
        assert exc is startup_error
    else:
        raise AssertionError("startup failure should be re-raised")

    assert commands == [
        release_golden_path.compose_command(
            config, "--profile", "api", "up", "--detach", "--build"
        ),
        release_golden_path.compose_command(config, "ps"),
        release_golden_path.compose_command(config, "logs", "--no-color", "--timestamps"),
    ]


def test_start_stack_diagnostics_do_not_mask_startup_failure(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    startup_error = release_golden_path.subprocess.CalledProcessError(1, ["docker", "compose"])

    def fail_commands(args, **kwargs):
        if args[-3:] == ["up", "--detach", "--build"]:
            raise startup_error
        raise RuntimeError("diagnostic failed")

    monkeypatch.setattr(release_golden_path, "run", fail_commands)

    try:
        release_golden_path.start_stack(config)
    except release_golden_path.subprocess.CalledProcessError as exc:
        assert exc is startup_error
    else:
        raise AssertionError("startup failure should be re-raised")


def test_runtime_diagnostics_emit_relevant_service_logs_and_do_not_mask_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    commands: list[list[str]] = []

    def fake_run(args, **_kwargs):
        commands.append(args)
        if args[-1] == "nessie":
            raise RuntimeError("log collection unavailable")

    monkeypatch.setattr(release_golden_path, "run", fake_run)

    release_golden_path.emit_runtime_diagnostics(config)

    assert commands == [
        release_golden_path.compose_command(config, "logs", "--no-color", "--timestamps", service)
        for service in release_golden_path.RUNTIME_DIAGNOSTIC_SERVICES
    ]
    assert "diagnostics failed for nessie: log collection unavailable" in capsys.readouterr().err


def test_align_project_name_binds_cli_lookup_to_owned_compose_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.project_dir.mkdir()
    (config.project_dir / "phlo.yaml").write_text("name: csv-batch\ndescription: demo\n")

    release_golden_path.align_project_name(config)

    assert (config.project_dir / "phlo.yaml").read_text().splitlines()[0] == (
        "name: phlo-qa001-test"
    )


def test_cleanup_only_tears_down_owned_compose_project(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.compose_file.parent.mkdir(parents=True)
    config.compose_file.write_text("services: {}\n", encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(release_golden_path, "run", lambda args, **_: commands.append(args))

    errors = release_golden_path.cleanup(config, owned_paths={config.project_dir})

    assert errors == []
    assert commands == [
        release_golden_path.compose_command(
            config, "--profile", "api", "down", "--volumes", "--remove-orphans"
        )
    ]
    assert not config.project_dir.exists()


def test_cleanup_removes_owned_paths_when_compose_down_fails(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.compose_file.parent.mkdir(parents=True)
    config.compose_file.write_text("services: {}\n", encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    operator_env = tmp_path / "operator-env"
    wheelhouse.mkdir()
    operator_env.mkdir()
    monkeypatch.setattr(
        release_golden_path,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down failed")),
    )

    errors = release_golden_path.cleanup(
        config,
        owned_paths={config.project_dir, wheelhouse, operator_env},
    )

    assert [str(error) for error in errors] == ["down failed"]
    assert not config.project_dir.exists()
    assert not wheelhouse.exists()
    assert not operator_env.exists()


def test_cleanup_uses_docker_for_root_owned_generated_files(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.project_dir.mkdir()
    calls = 0
    commands: list[list[str]] = []

    def remove(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("root-owned cache")
        path.rmdir()

    monkeypatch.setattr(release_golden_path.shutil, "rmtree", remove)
    monkeypatch.setattr(release_golden_path, "run", lambda args, **_: commands.append(args))

    errors = release_golden_path.cleanup(config, owned_paths={config.project_dir})

    assert errors == []
    assert commands == [
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{config.project_dir}:/cleanup",
            "alpine:3.24.1",
            "sh",
            "-c",
            "rm -rf /cleanup/* /cleanup/.[!.]* /cleanup/..?*",
        ]
    ]


def test_runtime_error_returns_nonzero_without_masking_cleanup_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = tmp_path / "project"

    def fail_validation(config: release_golden_path.RunConfig) -> None:
        config.project_dir.mkdir()
        config.compose_file.parent.mkdir()
        config.compose_file.write_text("services: {}\n", encoding="utf-8")
        raise RuntimeError("validation failed")

    monkeypatch.setattr(release_golden_path, "build_wheelhouse", fail_validation)
    monkeypatch.setattr(
        release_golden_path,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down failed")),
    )

    result = release_golden_path.main(["--repo-root", str(tmp_path), "--project-dir", str(project)])

    assert result == 1
    assert not project.exists()
    stderr = capsys.readouterr().err
    assert "release golden path failed: validation failed" in stderr
    assert "release golden path cleanup failed: down failed" in stderr


def test_unexpected_exception_still_cleans_owned_paths(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"

    def fail_unexpectedly(config: release_golden_path.RunConfig) -> None:
        config.project_dir.mkdir()
        raise ValueError("unexpected validation error")

    monkeypatch.setattr(release_golden_path, "build_wheelhouse", fail_unexpectedly)

    result = release_golden_path.main(["--repo-root", str(tmp_path), "--project-dir", str(project)])

    assert result == 1
    assert not project.exists()


def test_dagster_build_receives_a_local_wheelhouse_arg() -> None:
    service = (REPO_ROOT / "packages/phlo-dagster/src/phlo_dagster/service.yaml").read_text()
    daemon = (REPO_ROOT / "packages/phlo-dagster/src/phlo_dagster/dagster-daemon.yaml").read_text()
    dockerfile = (REPO_ROOT / "packages/phlo-dagster/src/phlo_dagster/Dockerfile").read_text()
    trino_service = (REPO_ROOT / "packages/phlo-trino/src/phlo_trino/service.yaml").read_text()
    api_service = (REPO_ROOT / "packages/phlo-api/src/phlo_api/service.yaml").read_text()
    api_dockerfile = (REPO_ROOT / "packages/phlo-api/src/phlo_api/Dockerfile").read_text()

    assert "PHLO_WHEELHOUSE: ${PHLO_WHEELHOUSE:-}" in service
    assert "PHLO_WHEELHOUSE: ${PHLO_WHEELHOUSE:-}" in daemon
    assert 'ARG PHLO_WHEELHOUSE=""' in dockerfile
    assert "FROM python:3.12-slim AS phlo-build-context" in dockerfile
    assert "COPY . ." in dockerfile
    assert "RUN mkdir -p /opt/phlo-build-context/wheelhouse" in dockerfile
    assert (
        "COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse"
        in dockerfile
    )
    assert "RUN --mount=" not in dockerfile
    assert "--no-index --no-deps --reinstall --find-links" in dockerfile
    assert '"phlo==$PHLO_VERSION"' in dockerfile
    local_reinstall = dockerfile.rindex(
        "uv pip install --system --no-index --no-deps --reinstall --find-links"
    )
    dependency_install = dockerfile.index(
        "uv pip install --system --prerelease explicit --find-links"
    )
    assert local_reinstall > dependency_install
    assert "- source: jvm.config" in trino_service
    assert "dest: trino/jvm.config" in trino_service
    assert "PHLO_WHEELHOUSE: ${PHLO_WHEELHOUSE:-}" in api_service
    assert "dockerfile: phlo-api/Dockerfile" in api_service
    assert "dest: phlo-api/Dockerfile" in api_service
    assert (
        "COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse"
        in api_dockerfile
    )
    assert "--no-index --no-deps --reinstall --find-links" in api_dockerfile


def test_dagster_stable_version_install_keeps_base_requirements_unconditional() -> None:
    dockerfile = (REPO_ROOT / "packages/phlo-dagster/src/phlo_dagster/Dockerfile").read_text()

    assert 'base_requirements=("phlo[defaults]==$PHLO_VERSION"' in dockerfile
    assert 'base_requirements+=("${prerelease_requirements[@]}")' in dockerfile
    assert 'uv pip install --system --prerelease explicit "${base_requirements[@]}"' in dockerfile
    assert 'if [ -n "$PHLO_PRERELEASE_REQUIREMENTS" ]; then' in dockerfile


def test_configure_non_dev_compose_uses_docker_ephemeral_ports(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    config.project_dir.mkdir()
    config.project_dir.joinpath(".phlo").mkdir()
    config.project_dir.joinpath(".phlo/.env.local").write_text("", encoding="utf-8")
    config.wheelhouse.mkdir()
    config.wheelhouse.joinpath("phlo.whl").write_text("wheel", encoding="utf-8")
    config.repo_root.joinpath("pyproject.toml").write_text(
        "[project]\nversion = '1.2.3'\n", encoding="utf-8"
    )
    monkeypatch.setattr(release_golden_path, "run", lambda *args, **kwargs: None)

    config.project_dir.joinpath("phlo.yaml").write_text("name: test\n", encoding="utf-8")
    release_golden_path.configure_non_dev_compose(config)

    env_local = config.project_dir.joinpath(".phlo/.env.local").read_text()
    assert all(f"{name}=0\n" in env_local for name in release_golden_path.PORT_NAMES)
    assert "PHLO_PROJECT=phlo-qa001-test\n" in env_local
    assert "PHLO_LOG_FILE_TEMPLATE=/tmp/phlo-{YMD}.log\n" in env_local
    assert "PHLO_AUTHENTICATION_PROVIDER=service_token\n" in env_local
    assert "PHLO_AUTH_SERVICE_ENABLED=true\n" in env_local
    assert "PHLO_AUTHORIZATION_BACKEND=default\n" in env_local
    assert "PHLO_AUTHORIZATION_MODE=required\n" in env_local
    token_config = next(
        line for line in env_local.splitlines() if line.startswith("PHLO_AUTH_SERVICE_TOKENS=")
    )
    configured_tokens = json.loads(token_config.split("=", 1)[1])
    assert configured_tokens == {}
