"""The YouTube pack runs through the same pipeline as the air pack."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from source_to_report.youtube.runner import run
from source_to_report.youtube.sources import read_videos


def _generate(root: Path) -> Path:
    data = root / "data"
    subprocess.run(
        [sys.executable, "scripts/generate_youtube_fixtures.py", "--data-dir", str(data)],
        check=True,
        capture_output=True,
    )
    return data


def _digest(root: Path) -> dict[str, str]:
    files = [root / "index.html", root / "summary.json"]
    files += sorted((root / "data").glob("*.csv"))
    files += sorted((root / "charts").glob("*.svg"))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def test_export_is_deterministic(tmp_path: Path) -> None:
    first = _generate(tmp_path / "a") / "youtube" / "channel.json"
    second = _generate(tmp_path / "b") / "youtube" / "channel.json"

    assert first.read_bytes() == second.read_bytes()


def test_read_videos_rejects_an_empty_export(tmp_path: Path) -> None:
    path = tmp_path / "youtube" / "channel.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"channel": {}, "videos": []}), encoding="utf-8")

    try:
        read_videos(tmp_path)
    except ValueError as error:
        assert "no videos" in str(error)
    else:  # pragma: no cover - the explicit failure path is the point
        raise AssertionError("an empty export was accepted")


def test_channel_brief_builds_and_is_reproducible(tmp_path: Path, monkeypatch) -> None:
    data = _generate(tmp_path / "fixtures")
    monkeypatch.setenv("SOURCE_TO_REPORT_DATA", str(data))
    export = json.loads((data / "youtube" / "channel.json").read_text(encoding="utf-8"))

    first = run(db_path=tmp_path / "a.duckdb", out_dir=tmp_path / "a", quiet=True)
    run(db_path=tmp_path / "b.duckdb", out_dir=tmp_path / "b", quiet=True)

    assert first["counts"]["videos"] == len(export["videos"]) == 24
    assert first["counts"]["views"] == sum(int(video["views"]) for video in export["videos"])
    assert first["counts"]["months"] == 12
    assert (tmp_path / "a" / "index.html").is_file()
    assert len(list((tmp_path / "a" / "charts").glob("*.svg"))) == 2
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    html = (tmp_path / "a" / "index.html").read_text(encoding="utf-8")
    assert "Channel brief" in html
    assert "<script" not in html
