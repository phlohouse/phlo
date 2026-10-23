"""Bounded retained CSV inspection and versioned column review; never loads a table."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, model_validator

from phlo.operations.csv_upload import (
    csv_value,
    parse_csv,
    csv_output_columns,
    csv_review_values,
    inspect_csv_columns,
)

from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import (
    audit_operation,
    enforce_rate_limit,
    project_root,
    require_scope,
)
from phlo_api.api.v1 import _target
from phlo_api.errors import (
    BackendUnavailableError,
    ConflictError,
    NotFoundError,
    UnprocessableInputError,
)
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 CSV uploads"])
MAX_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_UPLOADS = 100
MAX_ROWS = 50_000
MAX_COLUMNS = 100
MAX_CELLS = 500_000
MAX_PREVIEW_ROWS = 25
_COLLECTION = "v1_csv_uploads"
_UPLOAD_LOCK = asyncio.Semaphore(2)
_ID = re.compile(r"^[a-f0-9]{32}$")


class QualifierColumns(WireModel):
    value_name: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    flag_name: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")


class ColumnChoice(WireModel):
    column: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    type: Literal["string", "integer", "number", "boolean", "date"]
    nullable: bool = False
    date_format: Literal["iso", "day_first", "month_first"] = "iso"
    qualifiers: QualifierColumns | None = None

    def recipe(self) -> dict[str, Any]:
        """Keep established immutable recipe digests stable when no split was chosen."""
        return self.model_dump(exclude={"qualifiers"} if self.qualifiers is None else set())


class ColumnReview(WireModel):
    expected_version: int = Field(ge=1)
    columns: list[ColumnChoice] = Field(min_length=1, max_length=MAX_COLUMNS)

    @model_validator(mode="after")
    def unique_columns(self) -> ColumnReview:
        if len({column.column for column in self.columns}) != len(self.columns):
            raise ValueError("Choose each original column exactly once.")
        output = csv_output_columns([column.recipe() for column in self.columns])
        names = [column["name"] for column in output]
        if len(set(names)) != len(names):
            raise ValueError("Destination column names must be unique.")
        if len(names) > MAX_COLUMNS:
            raise ValueError("Original and derived output columns must not exceed 100.")
        if any(name.startswith(("_phlo_", "_dlt_")) for name in names):
            raise ValueError("Column names beginning _phlo_ or _dlt_ are reserved for provenance.")
        if any(column.qualifiers and column.type != "string" for column in self.columns):
            raise ValueError("Qualifier splitting must keep the original column as text.")
        return self


class CsvColumnError(WireModel):
    row: int = Field(ge=2)
    column: str
    message: str


class CsvUpload(WireModel):
    upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    env: Environment
    filename: str
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0, le=MAX_BYTES)
    actor: str
    created_at: str
    version: int = Field(ge=1)
    status: Literal["retained", "reviewed", "invalid"]
    columns: list[ColumnChoice] = Field(max_length=MAX_COLUMNS)
    row_count: int | None = Field(default=None, ge=0, le=MAX_ROWS)
    parse_errors: list[str] = Field(default_factory=list, max_length=10)
    error_count: int = Field(default=0, ge=0)
    errors: list[CsvColumnError] = Field(default_factory=list, max_length=100)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None


class UploadArchive(WireModel):
    archived: bool
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class CsvUploadPage(WireModel):
    env: Environment
    items: list[CsvUpload]
    total: int


class CsvOutputColumn(WireModel):
    name: str
    type: Literal["string", "integer", "number", "boolean", "date"]
    nullable: bool
    source_column: str
    derived: bool


class CsvDateExample(WireModel):
    original: str
    iso: str | None
    day_first: str | None
    month_first: str | None


class CsvDateRecognition(WireModel):
    iso: int
    day_first: int
    month_first: int
    ambiguous: int
    invalid: int
    examples: list[CsvDateExample]


class CsvQualifierRecognition(WireModel):
    numbers: int
    nd: int
    below_loq: int
    other: int
    blank: int


class CsvColumnAssistance(WireModel):
    column: str
    dates: CsvDateRecognition | None
    qualifiers: CsvQualifierRecognition | None


class CsvUploadView(CsvUpload):
    raw_columns: list[str]
    raw_rows: list[dict[str, str]]
    rows: list[dict[str, Any]]
    sample_limit: Literal[25] = MAX_PREVIEW_ROWS
    loaded: Literal[False] = False
    output_columns: list[CsvOutputColumn] = Field(default_factory=list, max_length=MAX_COLUMNS)
    column_assistance: list[CsvColumnAssistance] = Field(
        default_factory=list, max_length=MAX_COLUMNS
    )


def _legacy_path() -> Path:
    return project_root() / ".phlo" / "csv-uploads.json"


def _file_path(upload_id: str) -> Path:
    if not _ID.fullmatch(upload_id):
        raise NotFoundError("Upload was not found.")
    root = project_root().resolve()
    path = root / ".phlo" / "uploads" / upload_id / "source.csv"
    if not path.resolve().is_relative_to(root):
        raise UnprocessableInputError("Upload storage must remain inside the project.")
    return path


def _records() -> list[CsvUpload]:
    items = load_collection(project_root(), _COLLECTION, _legacy_path())
    if len(items) > MAX_UPLOADS:
        raise BackendUnavailableError("Upload collection exceeds its configured limit.")
    return [CsvUpload.model_validate(item) for item in items]


def _record(request: Request, env: str, upload_id: str) -> CsvUpload:
    _file_path(upload_id)
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    record = next(
        (
            item
            for item in _records()
            if item.env == env and item.upload_id == upload_id and item.actor == principal.subject
        ),
        None,
    )
    if record is None:
        raise NotFoundError("Upload was not found in this environment.")
    return record


def _verified_bytes(record: CsvUpload) -> bytes:
    try:
        with _file_path(record.upload_id).open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise BackendUnavailableError("Retained upload bytes are unavailable.") from exc
    if len(data) != record.byte_count or hashlib.sha256(data).hexdigest() != record.checksum:
        raise ConflictError("Retained upload checksum changed. Upload the original file again.")
    return data


def _parse(data: bytes) -> tuple[list[str], list[list[str]], list[str]]:
    return parse_csv(data, max_rows=MAX_ROWS, max_columns=MAX_COLUMNS, max_cells=MAX_CELLS)


def _value(value: str, choice: ColumnChoice) -> Any:
    return csv_value(value, choice.model_dump())


def _choices(header: list[str], rows: list[list[str]]) -> list[ColumnChoice]:
    choices = []
    for index, column in enumerate(header):
        name = re.sub(r"[^a-z0-9_]+", "_", column.casefold()).strip("_")[:60]
        name = name if name and not name[0].isdigit() else f"column_{index + 1}"
        used = {item.name for item in choices}
        suffix = 1
        base = name
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        choices.append(
            ColumnChoice(
                column=column,
                name=name,
                type="string",
                nullable=any(not row[index] for row in rows),
            )
        )
    return choices


def _review_rows(
    header: list[str], rows: list[list[str]], choices: list[ColumnChoice]
) -> tuple[list[dict[str, Any]], int, list[CsvColumnError]]:
    if {choice.column for choice in choices} != set(header) or len(choices) != len(header):
        raise UnprocessableInputError("Review must include every original CSV column exactly once.")
    preview: list[dict[str, Any]] = []
    errors: list[CsvColumnError] = []
    count = 0
    for row_number, values in enumerate(rows, start=2):
        original = dict(zip(header, values, strict=True))
        converted = {}
        for choice in choices:
            try:
                converted.update(csv_review_values(original[choice.column], choice.recipe()))
            except ValueError as exc:
                converted[choice.name] = None
                count += 1
                if len(errors) < 100:
                    errors.append(
                        CsvColumnError(row=row_number, column=choice.column, message=str(exc))
                    )
        if len(preview) < MAX_PREVIEW_ROWS:
            preview.append(converted)
    return preview, count, errors


def _view(record: CsvUpload, data: bytes) -> CsvUploadView:
    header, rows, parse_errors = _parse(data)
    preview = []
    if not parse_errors:
        preview, _, _ = _review_rows(header, rows, record.columns)
    return CsvUploadView(
        **record.model_dump(),
        raw_columns=header,
        raw_rows=[dict(zip(header, row, strict=True)) for row in rows[:MAX_PREVIEW_ROWS]],
        rows=preview,
        output_columns=[
            CsvOutputColumn.model_validate(column)
            for column in csv_output_columns([column.recipe() for column in record.columns])
        ],
        column_assistance=[
            CsvColumnAssistance.model_validate(column)
            for column in inspect_csv_columns(header, rows)
        ],
    )


def _upload_auth(request: Request) -> dict[str, Any]:
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    return {**require_scope(request, "project:write"), "subject": principal.subject}


def _audit(auth: dict[str, Any], record: CsvUpload, operation: str) -> None:
    audit_operation(
        operation=operation,
        target=f"{record.env}/{record.upload_id}",
        dry_run=False,
        auth=auth,
        payload={"checksum": record.checksum},
        result={"version": record.version, "status": record.status, "loaded": False},
    )


@router.get("/uploads/csv", response_model=CsvUploadPage)
def v1_csv_upload_list(
    request: Request, env: Environment = Query(), include_archived: bool = False
) -> CsvUploadPage:
    _target(request, env, allowed_query=frozenset({"env", "include_archived"}))
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    items = [
        item
        for item in _records()
        if item.env == env
        and item.actor == principal.subject
        and (include_archived or not item.archived_at)
    ]
    items.sort(key=lambda item: item.created_at, reverse=True)
    return CsvUploadPage(env=env, items=items, total=len(items))


@router.post("/uploads/csv/{upload_id}/archive", response_model=CsvUpload)
async def v1_csv_upload_archive(
    request: Request, upload_id: str, payload: UploadArchive, env: Environment = Query()
) -> CsvUpload:
    """Hide or restore evidence; never remove workflow inputs or mutation fences."""
    _target(request, env)
    auth = _upload_auth(request)
    async with _UPLOAD_LOCK:
        record = _record(request, env, upload_id)
        _verified_bytes(record)
        updated = record.model_copy(
            update={
                "archived_at": datetime.now(UTC).isoformat() if payload.archived else None,
                "archived_by": auth["subject"] if payload.archived else None,
                "archive_reason": payload.reason if payload.archived else None,
            }
        )

        def archive(items):
            current = next(item for item in items if item.get("upload_id") == upload_id)
            if CsvUpload.model_validate(current) != record:
                raise ConflictError("Upload changed before archival; reload before continuing.")
            return [updated.model_dump() if item is current else item for item in items]

        mutate_collection(project_root(), _COLLECTION, _legacy_path(), archive)
        audit_operation(
            operation="csv_upload.archive" if payload.archived else "csv_upload.restore",
            target=upload_id,
            dry_run=False,
            auth=auth,
            payload={"reason": payload.reason, "checksum": record.checksum},
            result={"archived_at": updated.archived_at, "evidence_retained": True},
        )
        return updated


@router.post("/uploads/csv", response_model=CsvUploadView, status_code=201)
async def v1_csv_upload(request: Request, env: Environment = Query()) -> CsvUploadView:
    _target(request, env)
    auth = _upload_auth(request)
    enforce_rate_limit(auth["subject"], "csv_upload")
    try:
        filename = unquote(request.headers.get("x-phlo-upload-name", "upload.csv"), errors="strict")
    except UnicodeDecodeError as exc:
        raise UnprocessableInputError("Upload filename is invalid.") from exc
    if (
        not filename.lower().endswith(".csv")
        or len(filename) > 255
        or any(character in filename for character in "/\\\x00\r\n")
    ):
        raise UnprocessableInputError("Choose a CSV filename without path separators.")
    async with _UPLOAD_LOCK:
        upload_id = uuid4().hex
        path = _file_path(upload_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
        committed = False
        try:
            digest = hashlib.sha256()
            byte_count = 0
            with path.open("xb") as handle:
                async for chunk in request.stream():
                    byte_count += len(chunk)
                    if byte_count > MAX_BYTES:
                        raise HTTPException(413, "CSV upload exceeds the 10 MiB limit.")
                    digest.update(chunk)
                    handle.write(chunk)
            path.chmod(0o600)
            data = path.read_bytes()
            header, rows, parse_errors = _parse(data)
            record = CsvUpload(
                upload_id=upload_id,
                env=env,
                filename=filename,
                checksum=digest.hexdigest(),
                byte_count=byte_count,
                actor=auth["subject"],
                created_at=datetime.now(UTC).isoformat(),
                version=1,
                status="invalid" if parse_errors else "retained",
                columns=_choices(header, rows),
                row_count=None if parse_errors else len(rows),
                parse_errors=parse_errors,
            )

            def append(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                if (
                    len(items) >= MAX_UPLOADS
                    or sum(CsvUpload.model_validate(item).byte_count for item in items) + byte_count
                    > MAX_TOTAL_BYTES
                ):
                    raise UnprocessableInputError(
                        "Project upload retention is full. The limit is 100 files or 100 MiB."
                    )
                return [*items, record.model_dump()]

            mutate_collection(project_root(), _COLLECTION, _legacy_path(), append)
            committed = True
            _audit(auth, record, "csv_upload.retain")
            return _view(record, data)
        finally:
            if not committed:
                path.unlink(missing_ok=True)
                path.parent.rmdir()


@router.get("/uploads/csv/{upload_id}", response_model=CsvUploadView)
def v1_csv_upload_detail(
    request: Request, upload_id: str, env: Environment = Query()
) -> CsvUploadView:
    _target(request, env)
    record = _record(request, env, upload_id)
    return _view(record, _verified_bytes(record))


@router.put("/uploads/csv/{upload_id}/columns", response_model=CsvUploadView)
def v1_csv_upload_review(
    request: Request, upload_id: str, payload: ColumnReview, env: Environment = Query()
) -> CsvUploadView:
    _target(request, env)
    auth = _upload_auth(request)
    record = _record(request, env, upload_id)
    if record.archived_at:
        raise ConflictError("Restore the archived upload before changing its review.")
    data = _verified_bytes(record)
    candidates = load_collection(
        project_root(), "v1_csv_candidates", project_root() / ".phlo/csv-candidates.json"
    )
    if any(item.get("upload_id") == upload_id for item in candidates):
        raise ConflictError(
            "This review has an immutable candidate destination. Upload again to change its recipe."
        )
    header, rows, parse_errors = _parse(data)
    if parse_errors:
        raise UnprocessableInputError(
            "Fix the CSV structure and upload it again before reviewing columns."
        )
    _, count, errors = _review_rows(header, rows, payload.columns)
    reviewed = record.model_copy(
        update={
            "version": record.version + 1,
            "columns": payload.columns,
            "status": "invalid" if count else "reviewed",
            "error_count": count,
            "errors": errors,
            "reviewed_by": auth["subject"],
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
    )

    def update(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = next(
            (
                item
                for item in items
                if item.get("env") == env and item.get("upload_id") == upload_id
            ),
            None,
        )
        if (
            current is None
            or current.get("version") != payload.expected_version
            or record.version != payload.expected_version
        ):
            raise ConflictError("Upload review changed. Reload before saving column choices.")
        return [reviewed.model_dump() if item is current else item for item in items]

    mutate_collection(project_root(), _COLLECTION, _legacy_path(), update)
    _audit(auth, reviewed, "csv_upload.review")
    return _view(reviewed, data)
