"""Exercise persistent workflow reload, launching and daemon storage over real gRPC/Postgres.

PHLO_TEST_POSTGRES_DSN must name a disposable, empty database when supplied.
Otherwise the test owns a bounded PostgreSQL container.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from importlib import resources
from threading import Event, Thread

import grpc
import psycopg2
import pytest
import yaml
from dagster import DagsterInstance, RunsFilter
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import WorkspaceFileTarget
from dagster._grpc.client import DagsterGrpcClient
from dagster_graphql.schema import create_schema
from dagster_graphql.test.utils import execute_dagster_graphql

from phlo_dagster.code_server import client_heartbeat_thread

pytestmark = pytest.mark.integration


def _wait(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(1)
    pytest.fail("Timed out waiting for the disposable Dagster lifecycle")


def _stop(process):
    process.terminate()
    process.wait(timeout=90)


def test_stalled_heartbeat_rpc_retries_before_the_worker_ttl():
    entered, release, recovered, shutdown = Event(), Event(), Event(), Event()
    attempts = 0

    def heartbeat(request, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            release.wait(timeout=45)
        else:
            recovered.set()
        return request

    with ThreadPoolExecutor(max_workers=2) as workers:
        server = grpc.server(workers)
        server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler(
                    "api.DagsterApi", {"Heartbeat": grpc.unary_unary_rpc_method_handler(heartbeat)}
                ),
            )
        )
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        thread = Thread(
            target=client_heartbeat_thread,
            args=(DagsterGrpcClient(port=port), shutdown),
        )
        thread.start()
        try:
            assert entered.wait(timeout=20)
            # A general 60s RPC deadline would miss the 30s worker TTL. The
            # bounded heartbeat must retry even while the first RPC is stuck.
            assert recovered.wait(timeout=20)
            assert thread.is_alive()
        finally:
            shutdown.set()
            release.set()
            thread.join(timeout=10)
            server.stop(grace=0).wait(timeout=5)
        assert not thread.is_alive()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets and process suspension")
@pytest.mark.parametrize("failure", ["heartbeat_expiry", "worker_killed"])
def test_active_worker_loss_exits_proxy_but_reload_does_not(tmp_path, failure):
    """A dead worker must release PID 1 for the configured Docker restart policy."""
    definition = yaml.safe_load(
        resources.files("phlo_dagster").joinpath("dagster-code.yaml").read_text()
    )["compose"]
    command = definition["command"][:3]
    command[0] = sys.executable
    assert definition["restart"] == "unless-stopped"
    # Application log routing may call arbitrary plugins. Proxy control-plane
    # logs must remain on Dagster's stdout handler, independent of those hooks.
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        f"from {command[2]} import main\n"
        "import logging\n"
        "class RejectRootRouting(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        raise RuntimeError('Proxy control plane reached application logging')\n"
        "logging.getLogger().addHandler(RejectRootRouting())\n"
        "main()\n"
    )
    command = [sys.executable, str(launcher)]
    worker_pid = tmp_path / "worker.pid"
    definitions = tmp_path / "definitions.py"
    definitions.write_text(
        "import os\nfrom pathlib import Path\nfrom dagster import Definitions\n"
        f"Path({str(worker_pid)!r}).write_text(str(os.getpid()))\n"
        "defs = Definitions()\n"
    )
    socket = str(tmp_path / "code.sock")
    command += ["-s", socket, "-f", str(definitions), "-d", str(tmp_path)]
    # Shorten only this disposable worker's TTL to exercise actual heartbeat
    # expiry without imposing a 30+ second delay on each regression run.
    command += ["--heartbeat-ttl", "2"]
    with (tmp_path / "code.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            client = DagsterGrpcClient(socket=socket)

            def ready():
                assert process.poll() is None, (tmp_path / "code.log").read_text()
                try:
                    return client.ping("ready")["echo"] == "ready"
                except Exception:
                    return False

            _wait(ready)
            original = client.get_server_id()
            client.reload_code(timeout=90)
            assert client.get_server_id() != original
            assert process.poll() is None
            assert client.ping("after-reload")["echo"] == "after-reload"
            if failure == "worker_killed":
                os.kill(int(worker_pid.read_text()), signal.SIGKILL)
            else:
                os.kill(process.pid, signal.SIGSTOP)
                # The worker keeps running while only its heartbeat sender is
                # suspended. Wait for the real expiry, not a mocked clock.
                deadline = time.monotonic() + 30
                while "No heartbeat received" not in (tmp_path / "code.log").read_text():
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                os.kill(process.pid, signal.SIGCONT)
            _wait(lambda: process.poll() is not None, timeout=30)
            assert process.returncode == 0
            assert "Active Dagster worker exited" in (tmp_path / "code.log").read_text()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGCONT)
                _stop(process)


def test_persistent_location_reload_restart_launch_and_pooled_daemon(tmp_path, monkeypatch):
    with ExitStack() as stack:
        dsn = os.environ.get("PHLO_TEST_POSTGRES_DSN")
        if not dsn:
            from testcontainers.postgres import PostgresContainer

            postgres = stack.enter_context(
                PostgresContainer("postgres:18-alpine", password="test").with_kwargs(
                    nano_cpus=500_000_000, mem_limit="256m"
                )
            )
            dsn = postgres.get_connection_url(driver=None)
        monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
        monkeypatch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
        (tmp_path / "dagster.yaml").write_text(
            yaml.safe_dump(
                {
                    "storage": {"postgres": {"postgres_url": dsn}},
                    "run_coordinator": {
                        "module": "phlo_dagster.daemon_identity",
                        "class": "PhloQueuedRunCoordinator",
                        "config": {"max_concurrent_runs": 2},
                    },
                    "telemetry": {"enabled": False},
                }
            )
        )
        definitions = tmp_path / "definitions.py"
        definitions.write_text(f"""from dagster import Definitions, job, op, sensor, RunRequest, DefaultSensorStatus, ScheduleDefinition
from pathlib import Path
@op
def write_result(context):
    Path({str(tmp_path)!r}, context.run_id + ".result").write_text("version-one")
    if context.run.tags.get("fail") == "true":
        raise RuntimeError("deliberate failure")
@job
def proof_job():
    write_result()
@sensor(job=proof_job, default_status=DefaultSensorStatus.RUNNING, minimum_interval_seconds=1)
def proof_sensor():
    yield RunRequest(run_key="once")
proof_schedule = ScheduleDefinition(job=proof_job, cron_schedule="* * * * *", name="proof_schedule")
defs = Definitions(jobs=[proof_job], sensors=[proof_sensor], schedules=[proof_schedule])
""")
        socket = str(tmp_path / "code.sock")
        command = [
            sys.executable,
            "-m",
            "phlo_dagster.code_server",
            "-s",
            socket,
            "-f",
            str(definitions),
            "-d",
            str(tmp_path),
        ]
        log = stack.enter_context((tmp_path / "processes.log").open("w"))

        def start_server():
            process = subprocess.Popen(command, stdout=log, stderr=log)
            stack.callback(lambda: _stop(process) if process.poll() is None else None)
            client = DagsterGrpcClient(socket=socket)

            def ready():
                try:
                    return client.ping("ready")["echo"] == "ready"
                except Exception:
                    return False

            _wait(ready)
            return process

        server = start_server()
        workspace = tmp_path / "workspace.yaml"
        workspace.write_text(
            yaml.safe_dump(
                {
                    "load_from": [
                        {
                            "grpc_server": {
                                "socket": socket,
                                "location_name": "proof",
                            }
                        }
                    ]
                }
            )
        )
        daemon = subprocess.Popen(
            [sys.executable, "-m", "phlo_dagster.daemon", "-w", str(workspace)],
            stdout=log,
            stderr=log,
        )
        stack.callback(lambda: _stop(daemon) if daemon.poll() is None else None)
        instance = stack.enter_context(DagsterInstance.get())
        context = stack.enter_context(
            WorkspaceProcessContext(instance, WorkspaceFileTarget([str(workspace)]))
        )
        _wait(lambda: any(run.is_finished for run in instance.get_runs()))
        sensor_run = instance.get_runs()[0]
        assert sensor_run.status.value == "SUCCESS"
        assert sensor_run.tags["dagster/sensor_name"] == "proof_sensor"
        assert (tmp_path / f"{sensor_run.run_id}.result").read_text() == "version-one"
        location = context.create_request_context().get_code_location("proof")
        server_id = location.client.get_server_id()
        context.refresh_workspace()
        assert (
            context.create_request_context().get_code_location("proof").client.get_server_id()
            == server_id
        )
        definitions.write_text(definitions.read_text().replace("version-one", "version-two"))
        context.reload_code_location("proof")

        def launch(fail=False):
            result = execute_dagster_graphql(
                context.create_request_context(),
                """mutation($params: ExecutionParams!) {
                  launchPipelineExecution(executionParams: $params) {
                    __typename ... on LaunchRunSuccess { run { runId } }
                  }
                }""",
                {
                    "params": {
                        "selector": {
                            "repositoryLocationName": "proof",
                            "repositoryName": "__repository__",
                            "pipelineName": "proof_job",
                        },
                        "runConfigData": {},
                        "executionMetadata": {
                            "tags": [{"key": "fail", "value": str(fail).lower()}]
                        },
                    }
                },
                schema=create_schema(),
            )
            payload = result.data["launchPipelineExecution"]
            assert payload["__typename"] == "LaunchRunSuccess", result.data
            return payload["run"]["runId"]

        success, failure = launch(), launch(fail=True)
        _wait(lambda: all(instance.get_run_by_id(run).is_finished for run in [success, failure]))
        assert instance.get_run_by_id(success).status.value == "SUCCESS"
        assert instance.get_run_by_id(failure).status.value == "FAILURE"
        assert (tmp_path / f"{success}.result").read_text() == "version-two"
        _stop(server)
        start_server()
        context.refresh_workspace()
        recovered = launch()
        _wait(lambda: instance.get_run_by_id(recovered).is_finished)
        assert instance.get_run_by_id(recovered).status.value == "SUCCESS"
        assert (tmp_path / f"{recovered}.result").read_text() == "version-two"
        assert daemon.poll() is None
        assert instance.get_daemon_heartbeats()["SENSOR"].timestamp > 0
        connection = stack.enter_context(psycopg2.connect(dsn))
        connection.autocommit = True

        # This database is owned by the test. Drop its idle pooled sessions,
        # not the server, then prove the daemon reconnects and launches again.
        heartbeat = instance.get_daemon_heartbeats()["SENSOR"].timestamp
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='idle'"
            )
        _wait(lambda: instance.get_daemon_heartbeats()["SENSOR"].timestamp > heartbeat)
        location = context.create_request_context().get_code_location("proof")
        schedule = location.get_repository("__repository__").get_schedule("proof_schedule")
        instance.start_schedule(schedule)
        schedule_filter = RunsFilter(tags={"dagster/schedule_name": "proof_schedule"})
        _wait(lambda: any(run.is_finished for run in instance.get_runs(filters=schedule_filter)))
        scheduled_run = instance.get_runs(filters=schedule_filter)[0]
        assert scheduled_run.status.value == "SUCCESS"
        assert (tmp_path / f"{scheduled_run.run_id}.result").read_text() == "version-two"
        instance.stop_schedule(schedule.get_remote_origin_id(), schedule.selector_id)
        assert daemon.poll() is None

        def sessions():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute(
                    "SELECT sessions FROM pg_stat_database WHERE datname=current_database()"
                )
                return cursor.fetchone()[0]

        time.sleep(5)
        before = sessions()
        time.sleep(35)
        new_sessions = sessions() - before
        assert new_sessions < 10, f"Idle daemon opened {new_sessions} new sessions in 35 seconds"
        (tmp_path / "evidence.json").write_text(json.dumps({"idle_new_sessions": new_sessions}))
