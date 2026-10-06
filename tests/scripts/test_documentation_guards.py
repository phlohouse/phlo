"""Exercise the repository's Markdown-link and ADR-index checks."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_adr_index = load_script("check_adr_index")
check_markdown_links = load_script("check_markdown_links")


def write_adr(directory: Path, number: str, status: str | None = "Accepted") -> None:
    record = directory / f"{number}-test.md"
    status_section = f"## Status\n\n**{status}**\n" if status else "## Context\n"
    record.write_text(f"# ADR {number}\n\n{status_section}")


def run_adr_check(
    tmp_path: Path, monkeypatch, index: str, *, records: tuple[str, ...] = ("0001", "0002")
) -> int:
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    for number in records:
        write_adr(decisions, number)
    index_path = decisions / "index.md"
    index_path.write_text(index)
    monkeypatch.setattr(check_adr_index, "DECISIONS", decisions)
    monkeypatch.setattr(check_adr_index, "INDEX", index_path)
    return check_adr_index.main()


def adr_row(number: str, target: str, status: str = "Accepted") -> str:
    return f"| [{number}]({target}) | {number}. Test ADR | {status} |\n"


def test_adr_index_accepts_matching_records_and_statuses(tmp_path, monkeypatch, capsys) -> None:
    code = run_adr_check(
        tmp_path,
        monkeypatch,
        adr_row("0001", "0001-test.md") + adr_row("0002", "0002-test.md"),
    )
    assert code == 0
    assert "records and statuses match" in capsys.readouterr().out


def test_adr_index_rejects_link_to_another_existing_record(tmp_path, monkeypatch, capsys) -> None:
    code = run_adr_check(
        tmp_path,
        monkeypatch,
        adr_row("0001", "0002-test.md") + adr_row("0002", "0002-test.md"),
    )
    assert code == 1
    assert "ADR 0001 links to 0002-test.md, expected 0001-test.md" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("index", "records", "missing_status", "expected_error"),
    [
        (
            adr_row("0001", "0001-test.md"),
            ("0001", "0002"),
            False,
            "ADR 0002 is missing from the index",
        ),
        (
            adr_row("0001", "0001-test.md") + adr_row("0003", "0003-missing.md"),
            ("0001",),
            False,
            "ADR 0003 links to a missing record: 0003-missing.md",
        ),
        (
            adr_row("0001", "0001-test.md", "Proposed") + adr_row("0002", "0002-test.md"),
            ("0001", "0002"),
            False,
            "ADR 0001 status mismatch: record=Accepted, index=Proposed",
        ),
        (
            adr_row("0001", "0001-test.md") + adr_row("0002", "0002-test.md"),
            ("0001", "0002"),
            True,
            "ADR 0002 has no recognised status",
        ),
    ],
)
def test_adr_index_rejects_missing_or_mismatched_records_and_statuses(
    tmp_path, monkeypatch, capsys, index, records, missing_status, expected_error
) -> None:
    run_adr_check(tmp_path, monkeypatch, index, records=records)
    decisions = check_adr_index.DECISIONS
    if missing_status:
        write_adr(decisions, "0002", status=None)

    code = check_adr_index.main()
    assert code == 1
    assert expected_error in capsys.readouterr().err


def test_markdown_links_accepts_relative_fragment_and_ignores_fences_and_external_urls(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "guide.md"
    target = tmp_path / "target.md"
    target.write_text("# Valid heading\n")
    source.write_text(
        "# Source heading\n\n"
        "[local](target.md#valid-heading)\n"
        "[same page](#source-heading)\n"
        "[external](https://example.com/missing.md#not-a-heading)\n"
        "```md\n[ignored](missing.md#bad)\n```\n"
    )
    monkeypatch.setattr(check_markdown_links, "ROOT", tmp_path)
    monkeypatch.setattr(check_markdown_links, "markdown_files", lambda: [source])

    assert check_markdown_links.main() == 0
    assert "All tracked Markdown links resolve" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("link", "expected_error"),
    [
        ("[missing](missing.md)", "target does not exist"),
        ("[bad anchor](target.md#missing)", "heading #missing does not exist"),
        ("[missing same-page anchor](#missing)", "heading #missing does not exist"),
    ],
)
def test_markdown_links_rejects_broken_relative_targets_and_fragments(
    tmp_path, monkeypatch, capsys, link, expected_error
) -> None:
    source = tmp_path / "guide.md"
    (tmp_path / "target.md").write_text("# Present heading\n")
    source.write_text(f"# Present heading\n\n{link}")
    monkeypatch.setattr(check_markdown_links, "ROOT", tmp_path)
    monkeypatch.setattr(check_markdown_links, "markdown_files", lambda: [source])

    assert check_markdown_links.main() == 1
    assert expected_error in capsys.readouterr().err


def test_markdown_links_find_broken_link_between_separate_fences(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "guide.md"
    source.write_text("```md\nexample\n```\n[broken](missing.md)\n```md\nexample\n```\n")
    monkeypatch.setattr(check_markdown_links, "ROOT", tmp_path)
    monkeypatch.setattr(check_markdown_links, "markdown_files", lambda: [source])

    assert check_markdown_links.main() == 1
    assert "missing.md: target does not exist" in capsys.readouterr().err
