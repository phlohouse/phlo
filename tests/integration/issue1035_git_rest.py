"""GitHub transport backed by stock Git; only the draft PR envelope is injected."""

import base64
import json
import os
import subprocess
from urllib.parse import parse_qs, unquote

import httpx


class LocalGitRest:
    def __init__(self, root):
        self.root = root
        self.writes = []
        self.pulls = []
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Disposable operator")
        self.git("config", "user.email", "operator@example.invalid")

    def git(self, *args, stdin=None, env=None):
        return (
            subprocess.check_output(
                ["/usr/bin/git", *args],
                cwd=self.root,
                input=stdin,
                env={**os.environ, **(env or {})},
                stderr=subprocess.PIPE,
            )
            .decode()
            .strip()
        )

    def commit(self):
        self.git("add", "workflows")
        self.git("commit", "-m", "Existing reviewed contract")
        return self.git("rev-parse", "HEAD")

    def handle(self, request):
        path = unquote(request.url.path.split("/repos/local/journey", 1)[1])
        body = json.loads(request.content) if request.content else {}
        status = 200
        if request.method == "GET":
            if path == "/pulls":
                head = parse_qs(request.url.query.decode())["head"][0].split(":", 1)[1]
                result = [p for p in self.pulls if p["head"]["ref"] == head]
            elif path.startswith("/git/ref/heads/"):
                try:
                    result = {
                        "object": {
                            "sha": self.git(
                                "rev-parse",
                                "--verify",
                                "refs/heads/" + path.removeprefix("/git/ref/heads/"),
                            )
                        }
                    }
                except subprocess.CalledProcessError:
                    status, result = 404, {}
            elif path.startswith("/git/commits/"):
                result = {
                    "tree": {"sha": self.git("rev-parse", path.rsplit("/", 1)[1] + "^{tree}")}
                }
            elif path.startswith("/contents/"):
                ref = parse_qs(request.url.query.decode())["ref"][0]
                try:
                    source = subprocess.check_output(
                        ["/usr/bin/git", "show", ref + ":" + path.removeprefix("/contents/")],
                        cwd=self.root,
                        stderr=subprocess.PIPE,
                    )
                    result = {"encoding": "base64", "content": base64.b64encode(source).decode()}
                except subprocess.CalledProcessError:
                    status, result = 404, {}
            else:
                raise AssertionError(path)
        else:
            assert request.method == "POST"
            self.writes.append(path)
            if path == "/git/blobs":
                result = {
                    "sha": self.git(
                        "hash-object", "-w", "--stdin", stdin=base64.b64decode(body["content"])
                    )
                }
            elif path == "/git/trees":
                env = {"GIT_INDEX_FILE": str(self.root / ".git" / "proposal-index")}
                self.git("read-tree", body["base_tree"], env=env)
                for item in body["tree"]:
                    self.git(
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        item["mode"],
                        item["sha"],
                        item["path"],
                        env=env,
                    )
                result = {"sha": self.git("write-tree", env=env)}
            elif path == "/git/commits":
                result = {
                    "sha": self.git(
                        "commit-tree",
                        body["tree"],
                        "-p",
                        body["parents"][0],
                        stdin=body["message"].encode(),
                    )
                }
            elif path == "/git/refs":
                self.git("update-ref", body["ref"], body["sha"], "0" * 40)
                result = {"ref": body["ref"]}
            elif path == "/pulls":
                result = {
                    **body,
                    "number": len(self.pulls) + 1,
                    "html_url": "https://github.com/local/journey/pull/1",
                    "base": {"ref": body["base"]},
                    "head": {"ref": body["head"], "repo": {"full_name": "local/journey"}},
                }
                self.pulls.append(result)
            else:
                raise AssertionError(path)
        return httpx.Response(status, json=result)

    def client(self, _config):
        return httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(self.handle)
        )
