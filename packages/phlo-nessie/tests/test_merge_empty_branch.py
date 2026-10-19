"""Defend empty-catalog publication without overwriting concurrent target commits."""

import json
from urllib.parse import urlparse

import pytest
import requests

from phlo_nessie.resource import NessieResource


class CatalogServer:
    """Model conditional reference updates at the HTTP boundary."""

    def __init__(self, target="empty", concurrent_target=None, config=None):
        self.refs = {"main": target, "staging": "validated-batch"}
        self.concurrent_target = concurrent_target
        self.config = {"noAncestorHash": "empty"} if config is None else config
        self.assignments = 0

    @staticmethod
    def response(status, payload):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response

    def get(self, url, **kwargs):
        path = urlparse(url).path
        if path == "/api/v2/config":
            return self.response(200, self.config)
        name = path.rsplit("/", 1)[-1]
        return self.response(200, {"hash": self.refs[name]})

    def post(self, url, **kwargs):
        if self.concurrent_target:
            self.refs["main"] = self.concurrent_target
        return self.response(404, {"errorCode": "REFERENCE_NOT_FOUND"})

    def put(self, url, *, params, json, **kwargs):
        self.assignments += 1
        if params["expectedHash"] != self.refs["main"]:
            return self.response(409, {"errorCode": "REFERENCE_CONFLICT"})
        self.refs["main"] = json["hash"]
        return self.response(204, {})

    def install(self, monkeypatch):
        monkeypatch.setattr(requests, "get", self.get)
        monkeypatch.setattr(requests, "post", self.post)
        monkeypatch.setattr(requests, "put", self.put)


def test_first_publication_fast_forwards_only_empty_target(monkeypatch):
    server = CatalogServer()
    server.install(monkeypatch)
    assert NessieResource("http://nessie").merge_branch("staging")
    assert server.refs == {"main": "validated-batch", "staging": "validated-batch"}


def test_concurrent_first_publication_is_not_overwritten(monkeypatch):
    server = CatalogServer(concurrent_target="other-batch")
    server.install(monkeypatch)
    assert not NessieResource("http://nessie").merge_branch("staging")
    assert server.refs["main"] == "other-batch"
    assert server.refs["staging"] == "validated-batch"


@pytest.mark.parametrize("target,config", [("existing", {}), ("empty", {}), ("empty", [])])
def test_unknown_or_nonempty_target_is_never_assigned(monkeypatch, target, config):
    server = CatalogServer(target=target, config=config)
    server.install(monkeypatch)
    assert not NessieResource("http://nessie").merge_branch("staging")
    assert server.refs["main"] == target
    assert server.assignments == 0


@pytest.mark.parametrize("move_at_request", [False, True])
def test_audited_hashes_reach_conditional_assignment(monkeypatch, move_at_request):
    server = CatalogServer(target="audited" if move_at_request else "newer")
    server.install(monkeypatch)

    def pinned_history(url, **kwargs):
        if not url.endswith("/history"):
            return server.get(url, **kwargs)
        assert "/staging@validated-batch/history" in url
        return server.response(200, {"logEntries": [{"parentCommitHash": "audited"}]})

    def conditional_assignment(url, *, params, json, **kwargs):
        if move_at_request:
            server.refs["main"] = "newer"
        # Even if the candidate moves, assignment must use its audited hash.
        server.refs["staging"] = "unaudited-later-write"
        assert json["hash"] == "validated-batch"
        if params["expectedHash"] != server.refs["main"]:
            return server.response(409, {"errorCode": "REFERENCE_CONFLICT"})
        server.refs["main"] = json["hash"]
        return server.response(200, {})

    monkeypatch.setattr(requests, "get", pinned_history)
    monkeypatch.setattr(requests, "put", conditional_assignment)
    assert not NessieResource("http://nessie").merge_branch(
        "staging", expected_target_hash="audited", expected_source_hash="validated-batch"
    )
    assert server.refs == {"main": "newer", "staging": "unaudited-later-write"}
    assert server.assignments == 0


@pytest.mark.parametrize(
    "history",
    [
        {"logEntries": [], "hasMore": False},
        {"logEntries": [], "hasMore": True},
        {"logEntries": [], "hasMore": True, "token": "repeated"},
    ],
)
def test_missing_or_truncated_ancestry_cannot_publish(monkeypatch, history):
    server = CatalogServer(target="audited")
    server.install(monkeypatch)
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kw: (
            server.response(200, history) if url.endswith("/history") else server.get(url, **kw)
        ),
    )
    assert not NessieResource("http://nessie").merge_branch(
        "staging", expected_target_hash="audited", expected_source_hash="validated-batch"
    )
    assert server.refs["main"] == "audited"
    assert server.assignments == 0


def test_fast_forward_publishes_only_the_pinned_source(monkeypatch):
    server = CatalogServer(target="audited")
    server.install(monkeypatch)
    server.refs["staging"] = "unaudited-later-write"
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kw: (
            server.response(
                200,
                {
                    "logEntries": [
                        {
                            "commitMeta": {"hash": "validated-batch"},
                            "parentCommitHash": "audited",
                        }
                    ]
                },
            )
            if url.endswith("/history")
            else server.get(url, **kw)
        ),
    )
    assert NessieResource("http://nessie").merge_branch(
        "staging", expected_target_hash="audited", expected_source_hash="validated-batch"
    )
    assert server.refs == {"main": "validated-batch", "staging": "unaudited-later-write"}
