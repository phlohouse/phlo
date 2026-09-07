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
