"""CSV retention and review preserve bytes, reject unsafe inputs, and never load data."""

import hashlib
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from phlo_api.api import v1_uploads as uploads
from phlo_api.main import app
from security_test_support import authenticated_client


@pytest.fixture
def project(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"sandbox"},"prod":{"dagster_location":"prod","nessie_ref":"main"}}',
    )
    return tmp_path


def retain(data=b"batch_id,result,pull_date\nCGT04,98.2,24/09/2026\nCGT04,ND,2026-09-24\n"):
    return authenticated_client("operator").post(
        "/api/v1/uploads/csv?env=sandbox",
        content=data,
        headers={"Content-Type": "text/csv", "X-Phlo-Upload-Name": "stability.csv"},
    )


def test_exact_bytes_checksum_and_safe_generated_path_survive_reads(project):
    raw = b'\xef\xbb\xbfbatch_id,result\r\n"CGT04",<LOQ\r\n'
    response = retain(raw)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["checksum"] == hashlib.sha256(raw).hexdigest()
    assert record["actor"] == "test:operator"
    assert record["status"] == "retained" and record["loaded"] is False
    assert record["raw_rows"] == [{"batch_id": "CGT04", "result": "<LOQ"}]
    path = uploads._file_path(record["upload_id"])
    assert path.read_bytes() == raw
    assert path.is_relative_to(project)
    assert path.stat().st_mode & 0o777 == 0o600
    fetched = authenticated_client("operator").get(
        f"/api/v1/uploads/csv/{record['upload_id']}?env=sandbox"
    )
    assert fetched.status_code == 200 and fetched.json()["checksum"] == record["checksum"]
    assert (
        authenticated_client("viewer")
        .get(f"/api/v1/uploads/csv/{record['upload_id']}?env=prod")
        .status_code
        == 404
    )


def test_review_scans_all_rows_preserves_flags_and_uses_cas(project):
    # The invalid value is beyond the 25-row preview, so a sample-only validator would miss it.
    raw = (
        "batch_id,result,pull_date\n" + "CGT04,98.2,24/09/2026\n" * 25 + "CGT04,ND,2026-09-24\n"
    ).encode()
    record = retain(raw).json()
    choices = record["columns"]
    choices[1]["type"] = "number"
    choices[2].update(type="date", date_format="day_first")
    url = f"/api/v1/uploads/csv/{record['upload_id']}/columns?env=sandbox"
    response = authenticated_client("operator").put(
        url, json={"expected_version": 1, "columns": choices}
    )
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["status"] == "invalid" and reviewed["error_count"] == 1
    assert reviewed["errors"][0]["row"] == 27
    assert reviewed["rows"][0]["pull_date"] == "2026-09-24"
    assert reviewed["version"] == 2 and reviewed["loaded"] is False
    assert uploads._file_path(record["upload_id"]).read_bytes() == raw
    assert (
        authenticated_client("operator")
        .put(url, json={"expected_version": 1, "columns": choices})
        .status_code
        == 409
    )
    choices[1]["type"] = "string"
    valid = (
        authenticated_client("operator")
        .put(url, json={"expected_version": 2, "columns": choices})
        .json()
    )
    assert valid["status"] == "reviewed" and valid["error_count"] == 0 and valid["row_count"] == 26
    assert valid["reviewed_by"] == "test:operator"


def test_tampered_bytes_cannot_be_read_or_reviewed(project):
    record = retain().json()
    uploads._file_path(record["upload_id"]).write_bytes(b"changed,data\n1,2\n")
    path = f"/api/v1/uploads/csv/{record['upload_id']}"
    assert authenticated_client("operator").get(f"{path}?env=sandbox").status_code == 409
    assert (
        authenticated_client("operator")
        .put(
            f"{path}/columns?env=sandbox",
            json={"expected_version": 1, "columns": record["columns"]},
        )
        .status_code
        == 409
    )


def test_paths_authentication_and_permissions_are_not_bypassed(project):
    assert (
        TestClient(app).post("/api/v1/uploads/csv?env=sandbox", content=b"a\n1\n").status_code
        == 401
    )
    assert (
        authenticated_client("viewer")
        .post("/api/v1/uploads/csv?env=sandbox", content=b"a\n1\n")
        .status_code
        == 403
    )
    for filename in ["../outside.csv", "folder\\outside.csv", "data.xlsx"]:
        response = authenticated_client("operator").post(
            "/api/v1/uploads/csv?env=sandbox",
            content=b"a\n1\n",
            headers={"X-Phlo-Upload-Name": quote(filename, safe="")},
        )
        assert response.status_code == 422
    record = retain().json()
    assert (
        authenticated_client("viewer")
        .put(
            f"/api/v1/uploads/csv/{record['upload_id']}/columns?env=sandbox",
            json={"expected_version": 1, "columns": record["columns"]},
        )
        .status_code
        == 403
    )
    assert not (project.parent / "outside.csv").exists()


def test_size_and_retention_limits_remove_incomplete_files(project, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 8)
    assert retain(b"a\n123456789\n").status_code == 413
    assert not list((project / ".phlo/uploads").glob("*/source.csv"))
    monkeypatch.setattr(uploads, "MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(uploads, "MAX_UPLOADS", 1)
    assert retain(b"a\n1\n").status_code == 201
    assert retain(b"a\n2\n").status_code == 422
    assert len(list((project / ".phlo/uploads").glob("*/source.csv"))) == 1


def test_structure_errors_and_duplicate_review_names_are_explicit(project, monkeypatch):
    invalid = retain(b"a,b\n1\n").json()
    assert invalid["status"] == "invalid" and invalid["parse_errors"]
    assert invalid["loaded"] is False
    record = retain().json()
    columns = record["columns"]
    columns[1]["name"] = columns[0]["name"]
    assert (
        authenticated_client("operator")
        .put(
            f"/api/v1/uploads/csv/{record['upload_id']}/columns?env=sandbox",
            json={"expected_version": 1, "columns": columns},
        )
        .status_code
        == 422
    )
    monkeypatch.setattr(uploads, "MAX_ROWS", 1)
    limited = retain(b"a\n1\n2\n").json()
    assert limited["status"] == "invalid" and "limit" in limited["parse_errors"][0]


def test_raw_drafts_are_actor_private_without_an_admin_bypass(project):
    record = retain().json()
    path = f"/api/v1/uploads/csv/{record['upload_id']}"
    review = {"expected_version": 1, "columns": record["columns"]}
    other = authenticated_client("operator")
    other.headers["x-test-subject"] = "another-operator"
    assert other.get(f"{path}?env=sandbox").status_code == 404
    assert other.put(f"{path}/columns?env=sandbox", json=review).status_code == 404
    assert authenticated_client("admin").get(f"{path}?env=sandbox").status_code == 404
    assert authenticated_client("viewer").get(f"{path}?env=sandbox").status_code == 404
    assert authenticated_client("operator").get(f"{path}?env=sandbox").json()["version"] == 1


def test_reviewed_date_and_qualifier_assistance_uses_full_file_and_preserves_raw_text(project):
    data = b"result,pull_date\r\n98.20,24/09/2026\r\n<LOQ,2026-09-24\r\nND,03/04/2026\r\n"
    record = retain(data).json()
    profiles = {item["column"]: item for item in record["column_assistance"]}
    assert profiles["pull_date"]["dates"]["day_first"] == 1
    assert profiles["pull_date"]["dates"]["iso"] == 1
    assert profiles["pull_date"]["dates"]["ambiguous"] == 1
    assert profiles["result"]["qualifiers"]["numbers"] == 1
    assert profiles["result"]["qualifiers"]["nd"] == 1
    assert profiles["result"]["qualifiers"]["below_loq"] == 1
    columns = record["columns"]
    columns[0]["qualifiers"] = {"value_name": "result_value", "flag_name": "result_flag"}
    columns[1].update(type="date", date_format="day_first")
    path = f"/api/v1/uploads/csv/{record['upload_id']}"
    response = authenticated_client("operator").put(
        path + "/columns?env=sandbox", json={"expected_version": 1, "columns": columns}
    )
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["status"] == "reviewed"
    assert reviewed["rows"][0] == {
        "result": "98.20",
        "result_value": 98.2,
        "result_flag": None,
        "pull_date": "2026-09-24",
    }
    assert (
        reviewed["rows"][1]["result_value"] is None and reviewed["rows"][1]["result_flag"] == "<LOQ"
    )
    assert reviewed["rows"][2]["pull_date"] == "2026-04-03"
    assert [column["name"] for column in reviewed["output_columns"]] == [
        "result",
        "result_value",
        "result_flag",
        "pull_date",
    ]
    assert reviewed["raw_rows"][0]["pull_date"] == "24/09/2026"
    assert (project / ".phlo/uploads" / record["upload_id"] / "source.csv").read_bytes() == data
    from phlo_dlt.csv_upload import reviewed_rows

    runtime = reviewed_rows(
        project,
        {
            "upload_id": record["upload_id"],
            "checksum": record["checksum"],
            "columns": columns,
            "row_count": 3,
            "record_key": "record_key",
        },
    )
    assert runtime[0]["result"] == "98.20" and runtime[0]["result_value"] == 98.2
    assert (
        runtime[1]["result_flag"] == "<LOQ" and runtime[2]["pull_date"].isoformat() == "2026-04-03"
    )


def test_qualifier_output_names_and_unknown_flags_fail_closed_across_the_full_file(project):
    data = b"result\n" + b"98.2\n" * 25 + b"ND\n<LOQ\nBLQ\n"
    record = retain(data).json()
    columns = record["columns"]
    columns[0]["qualifiers"] = {"value_name": "result_value", "flag_name": "result_flag"}
    path = f"/api/v1/uploads/csv/{record['upload_id']}/columns?env=sandbox"
    client = authenticated_client("operator")
    response = client.put(path, json={"expected_version": 1, "columns": columns})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "invalid" and response.json()["errors"][0]["row"] == 29
    columns[0]["qualifiers"]["flag_name"] = "result"
    assert client.put(path, json={"expected_version": 2, "columns": columns}).status_code == 422
    columns[0]["qualifiers"] = {"value_name": "result_value", "flag_name": "result_flag"}
    columns[0]["type"] = "number"
    assert client.put(path, json={"expected_version": 2, "columns": columns}).status_code == 422


def test_unselected_qualifier_extension_preserves_existing_candidate_recipe_digest(project):
    import hashlib
    import json
    from phlo_api.api.v1_upload_ingestion import _recipe

    retain()
    record = uploads._records()[0]
    old_columns = [
        {key: value for key, value in column.model_dump().items() if key != "qualifiers"}
        for column in record.columns
    ]
    old = {
        "parser_version": 1,
        "checksum": record.checksum,
        "version": record.version,
        "columns": old_columns,
    }
    expected = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert _recipe(record) == expected
