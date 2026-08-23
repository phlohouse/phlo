"""Tests for "phlo doctor": probes, JSON payloads, and remediation hints.

The startup fast-path must detect doctor invocations before plugin command discovery
runs, so a broken plugin cannot block diagnostics.
"""

from subprocess import CompletedProcess, TimeoutExpired

import yaml
from click.testing import CliRunner

from phlo.cli.commands.doctor import (
    DiagnosticResult,
    DiagnosticStatus,
    check_environment,
    doctor_cmd,
    run_diagnostics,
)
from phlo.cli.commands.services.ports import PortMapping
from phlo.cli.main import _is_doctor_invocation, cli


def test_diagnostic_result_serializes_to_json_payload() -> None:
    result = DiagnosticResult(
        id="env.docker",
        group="Environment",
        status=DiagnosticStatus.FAIL,
        message="Docker daemon is not reachable",
        fix="Start Docker Desktop, then run phlo doctor again.",
        details={"returncode": 1},
    )

    assert result.to_payload() == {
        "id": "env.docker",
        "group": "Environment",
        "status": "fail",
        "message": "Docker daemon is not reachable",
        "fix": "Start Docker Desktop, then run phlo doctor again.",
        "details": {"returncode": 1},
    }


def test_doctor_json_outputs_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.doctor.run_diagnostics",
        lambda verbose=False: [
            DiagnosticResult("env.python", "Environment", DiagnosticStatus.OK, "Python 3.13.5"),
            DiagnosticResult("env.docker", "Environment", DiagnosticStatus.FAIL, "Docker missing"),
        ],
    )

    result = CliRunner().invoke(doctor_cmd, ["--json"])

    assert result.exit_code == 1
    assert '"ok": 1' in result.output
    assert '"fail": 1' in result.output
    assert '"env.docker"' in result.output


def test_doctor_json_suppresses_probe_stdout(monkeypatch) -> None:
    def noisy_diagnostics(verbose=False):
        print("probe log line")
        return [
            DiagnosticResult("env.python", "Environment", DiagnosticStatus.OK, "Python 3.13.5"),
        ]

    monkeypatch.setattr("phlo.cli.commands.doctor.run_diagnostics", noisy_diagnostics)

    result = CliRunner().invoke(doctor_cmd, ["--json"])

    assert result.exit_code == 0
    assert result.output.startswith("{")
    assert "probe log line" not in result.output


def test_doctor_is_registered_on_root_cli(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.doctor.run_diagnostics",
        lambda verbose=False: [
            DiagnosticResult(
                "doctor.bootstrap", "Environment", DiagnosticStatus.OK, "Doctor command loaded"
            )
        ],
    )

    result = CliRunner().invoke(cli, ["doctor", "--json"])

    assert result.exit_code == 0
    assert '"doctor.bootstrap"' in result.output


def test_doctor_outside_project_points_to_init(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 0, "ok", ""),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "phlo.yaml not found" in result.output
    assert "Run this command inside a Phlo project, or create one with phlo init." in result.output


def test_doctor_missing_generated_services_points_to_services_init(tmp_path, monkeypatch) -> None:
    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 0, "ok", ""),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    result = CliRunner().invoke(cli, ["doctor"])

    assert "phlo services init" in result.output
    assert ".phlo/docker-compose.yml is missing" in result.output


def test_doctor_fails_when_generated_compose_is_malformed(tmp_path, monkeypatch) -> None:
    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / "docker-compose.yml").write_text("services: [unterminated\n")
    (tmp_path / ".phlo" / ".env").write_text("")
    (tmp_path / ".phlo" / ".env.local").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 0, "ok", ""),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    result = CliRunner().invoke(cli, ["doctor", "--json"])

    assert result.exit_code == 1
    assert '"project.compose"' in result.output
    assert ".phlo/docker-compose.yml could not be read or parsed" in result.output
    assert "Traceback" not in result.output


def test_doctor_invocation_skips_plugin_command_discovery() -> None:
    assert _is_doctor_invocation(["phlo", "doctor", "--json"])
    assert not _is_doctor_invocation(["phlo", "services", "list"])
    assert not _is_doctor_invocation(["phlo", "services", "exec", "doctor", "--", "true"])
    assert not _is_doctor_invocation(["phlo", "--help", "doctor"])


def test_environment_checks_report_missing_docker(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.doctor.shutil.which",
        lambda name: None if name == "docker" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 0, "Docker Compose version v2.0.0", ""),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    results = [result for result in run_diagnostics() if result.group == "Environment"]

    assert any(
        result.id == "env.docker.cli" and result.status == DiagnosticStatus.FAIL
        for result in results
    )
    assert any(result.id == "env.uv" and result.status == DiagnosticStatus.OK for result in results)


def test_environment_checks_report_compose_failure(monkeypatch) -> None:
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 1, "", "compose missing"),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    results = run_diagnostics()

    assert any(
        result.id == "env.docker.compose" and result.status == DiagnosticStatus.FAIL
        for result in results
    )


def test_environment_checks_report_compose_probe_timeout(monkeypatch) -> None:
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: (_ for _ in ()).throw(
            TimeoutExpired(command, timeout=10, output="", stderr="timed out")
        ),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    results = check_environment(verbose=True)

    failure = next(result for result in results if result.id == "env.docker.compose")
    assert failure.status == DiagnosticStatus.FAIL
    assert "probe failed" in failure.message
    assert failure.details["type"] == "TimeoutExpired"


def test_environment_checks_use_podman_backend_from_phlo_yaml(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump({"infrastructure": {"container_backend": "podman"}})
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.doctor.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"podman", "uv"} else None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._run_probe",
        lambda command: CompletedProcess(command, 0, "ok", ""),
    )
    monkeypatch.setattr("phlo.cli.commands.doctor.shutil.disk_usage", lambda path: (100, 50, 50))

    results = [result for result in check_environment() if result.group == "Environment"]

    assert any(
        result.id == "env.container_backend"
        and result.status == DiagnosticStatus.OK
        and result.message == "Container backend: podman"
        for result in results
    )
    assert any(
        result.id == "env.podman.compose" and result.status == DiagnosticStatus.OK
        for result in results
    )
    assert not any(result.id == "env.docker.cli" for result in results)


def test_project_checks_report_missing_services_init(tmp_path, monkeypatch) -> None:
    (tmp_path / "phlo.yaml").write_text(yaml.safe_dump({"name": "demo"}))
    monkeypatch.chdir(tmp_path)

    results = run_diagnostics()

    assert any(
        result.id == "project.config" and result.status == DiagnosticStatus.OK for result in results
    )
    assert any(
        result.id == "project.compose" and result.status == DiagnosticStatus.WARN
        for result in results
    )


def test_discovery_check_summarizes_exception(monkeypatch) -> None:
    class BrokenDiscovery:
        def discover(self):
            raise RuntimeError("entry point exploded")

    monkeypatch.setattr("phlo.cli.commands.doctor.ServiceDiscovery", BrokenDiscovery)

    results = run_diagnostics(verbose=False)

    assert any(
        result.id == "discovery.services" and result.status == DiagnosticStatus.FAIL
        for result in results
    )
    failure = next(result for result in results if result.id == "discovery.services")
    # Non-verbose runs summarize failures; raw exception text stays out of
    # default output.
    assert "entry point exploded" not in failure.message


def test_discovery_check_reports_entry_point_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._collect_service_plugin_failures",
        lambda: [
            {
                "plugin_name": "broken",
                "entry_point": "broken:Plugin",
                "plugin_type": "service",
                "error": "exploded",
                "error_type": "RuntimeError",
            }
        ],
    )

    results = run_diagnostics(verbose=False)

    failure = next(result for result in results if result.id == "discovery.entry_points")
    assert failure.status == DiagnosticStatus.FAIL
    assert "1 service plugin entry point" in failure.message
    assert failure.details == {}


def test_port_checks_report_conflicts(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.doctor._collect_port_mappings",
        lambda: [
            PortMapping("postgres", 5432, 5432, "default", "Running"),
            PortMapping("trino", 5432, 8080, "env", "Running", env_var="TRINO_PORT"),
        ],
    )

    results = run_diagnostics()

    assert any(
        result.id == "ports.conflicts" and result.status == DiagnosticStatus.FAIL
        for result in results
    )


def test_live_checks_skip_without_compose_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    results = run_diagnostics()

    assert any(
        result.id == "live.services" and result.status == DiagnosticStatus.SKIP
        for result in results
    )
