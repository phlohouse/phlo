"""Tie the reloadable Dagster worker's lifetime to its container.

Dagster 1.13 leaves its proxy serving after the active worker exits. Docker
cannot restart that container. Exit the proxy only on confirmed process death,
not on a slow RPC, and retain Dagster's normal reload and run-draining behaviour.
These process-local adaptations use Dagster internals and need lifecycle tests
when upgrading Dagster.
"""

from __future__ import annotations

import logging
import time
from threading import Event

from dagster._cli.code_server import start_command
from dagster._core.errors import DagsterUserCodeUnreachableError
from dagster._core.remote_representation.grpc_server_registry import ServerRegistryEntry
from dagster._grpc import client as grpc_client
from dagster._grpc import proxy_server
from dagster._grpc.__generated__ import dagster_api_pb2

logger = logging.getLogger("dagster.code_server")


def client_heartbeat_thread(client: grpc_client.DagsterGrpcClient, shutdown_event: Event) -> None:
    """Use an RPC deadline below the default worker TTL and expose missed beats."""
    last_completed = time.monotonic()
    last_attempt_finished = last_completed
    while not shutdown_event.wait(grpc_client.CLIENT_HEARTBEAT_INTERVAL):
        started = time.monotonic()
        gap = started - last_attempt_finished
        if gap > 5:
            logger.warning("Worker heartbeat scheduling gap: %.1fs", gap)
        try:
            # Dagster's default heartbeat uses the general 60s RPC deadline,
            # longer than the default 30s worker lifetime without heartbeats.
            client._query("Heartbeat", dagster_api_pb2.PingRequest, timeout=5, echo="ping")
        except DagsterUserCodeUnreachableError as exc:
            logger.warning(
                "Worker heartbeat RPC failed after %.1fs; last completion %.1fs ago: %s",
                time.monotonic() - started,
                time.monotonic() - last_completed,
                exc,
            )
        else:
            last_completed = time.monotonic()
        last_attempt_finished = time.monotonic()


class PhloProxyApiServicer(proxy_server.DagsterProxyApiServicer):
    """Let the existing container restart policy recover a lost active worker."""

    def _cleanup_thread(self) -> None:
        while not self._server_termination_event.wait(proxy_server.CLEANUP_TICK):
            # A reload deliberately retires the old worker. Only inspect the
            # current registry entry, under the same lock as ReloadCode.
            with self._reload_lock:
                if self._client:
                    entry = self._grpc_server_registry.get_grpc_server_entry(self._origin)
                    if isinstance(entry, ServerRegistryEntry):
                        exit_code = entry.process.server_process.poll()
                        if exit_code is not None:
                            logger.error(
                                "Active Dagster worker exited with code %s; stopping proxy "
                                "so the container restart policy can recover it",
                                exit_code,
                            )
                            self._server_termination_event.set()
                            return
                if self._shutdown_once_executions_finish_event.is_set():
                    if self._grpc_server_registry.are_all_servers_shut_down():
                        self._server_termination_event.set()
                        self._grpc_server_registry.shutdown_all_processes()


def main() -> None:
    """Run Dagster's unchanged code-server CLI with container lifecycle ownership."""
    setattr(grpc_client, "client_heartbeat_thread", client_heartbeat_thread)
    setattr(proxy_server, "DagsterProxyApiServicer", PhloProxyApiServicer)
    # Dagster configures its own stdout handler. Do not let proxy heartbeat or
    # shutdown logs synchronously discover/call application hooks on root.
    logging.getLogger("dagster").propagate = False
    start_command()


if __name__ == "__main__":
    main()
