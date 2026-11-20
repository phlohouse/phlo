"""Signed notebook schedules.

A schedule runs a notebook unattended as the person who set it, so the notebook
file alone must never be able to name that person: anyone who can change the
file (an editor, a git pull) could otherwise run code under someone else's
identity. The API signs each schedule with ``PHLO_NOTEBOOK_SCHEDULE_KEY`` over
the schedule itself, the notebook's path and the notebook's code and packages.
The scheduler runs only schedules whose signature still matches, so editing a
scheduled notebook pauses its schedule until someone who may schedule it saves
or sets it again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace

from phlo_api import notebook_files as files


def code_digest(document: files.NotebookDocument) -> str:
    """Hash of what a run would execute: every cell's kind, engine, name and source, and packages."""
    payload = {
        "cells": [
            [cell.kind, cell.engine, cell.name, cell.source]
            for cell in document.cells
            if cell.kind != "markdown"
        ],
        "packages": list(document.settings.packages),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _message(path: str, schedule: files.NotebookSchedule, digest: str) -> bytes:
    payload = {
        "v": 1,
        "path": path,
        "cron": schedule.cron,
        "env": schedule.env,
        "owner": schedule.owner,
        "enabled": schedule.enabled,
        "since": schedule.since,
        "query": schedule.query,
        "code": digest,
    }
    return json.dumps(payload, sort_keys=True).encode()


def sign(
    key: str, path: str, schedule: files.NotebookSchedule, document: files.NotebookDocument
) -> files.NotebookSchedule:
    """The schedule with a signature over it and ``document``'s code at ``path``."""
    mac = hmac.new(key.encode(), _message(path, schedule, code_digest(document)), "sha256")
    return replace(schedule, signature=mac.hexdigest())


def verify(key: str | None, document: files.NotebookDocument) -> bool:
    """Whether ``document``'s schedule was signed by this API for exactly this code and path."""
    schedule = document.settings.schedule
    if not key or schedule is None or not schedule.signature:
        return False
    unsigned = replace(schedule, signature=None)
    expected = hmac.new(
        key.encode(), _message(document.path, unsigned, code_digest(document)), "sha256"
    ).hexdigest()
    return hmac.compare_digest(expected, schedule.signature)


def needs_query(document: files.NotebookDocument) -> bool:
    return any(cell.kind == "sql" and cell.engine != "duckdb" for cell in document.cells)
