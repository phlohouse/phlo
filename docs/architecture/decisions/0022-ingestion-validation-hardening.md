# 22. Ingestion validation hardening & cleanup

Date: 2025-12-18

## Status

Proposed

## Context

We have identified technical debt and potential data quality risks in our data ingestion pipelines:

1.  **Loose Validation**: `validate_with_pandera` currently catches schema validation errors and continues with a warning. This hides data quality issues and prevents pipelines from "failing fast" when bad data is ingested.
2.  **Deprecated Code**: `add_phlo_timestamp` is deprecated in favor of `inject_metadata_columns`, but still exists in the codebase.
3.  **Lack of Control**: Users cannot configure whether a pipeline should fail or warn on validation errors.

Related beads:

- `phlo-nwk.4.3`: Make ingestion validation failures actionable (configurable strictness)
- `phlo-nwk.4.4`: Remove deprecated `add_phlo_timestamp` path

## Decision

We will harden the ingestion process by introducing strict validation controls and removing deprecated code.

### 1. Configurable Strict Validation

We will add a `strict_validation` parameter to the `phlo.ingest.dlt(...)` decorator and update the underlying validation logic.

- **By Default (Strict)**: `strict_validation=True`. Pipelines will FAIL if Pandera validation fails. This ensures data quality is enforced by default.
- **Opt-out (Warn)**: Users can set `strict_validation=False` to preserve the old behavior (log warning and continue), useful for legacy pipelines or "best effort" ingestion.

#### Changes in `decorator.py`

```python
def phlo_ingestion(
    # ...
    strict_validation: bool = True,  # Default to True for safety
    # ...
)
```

The decorator wrapper will use this flag to decide whether to raise a `dagster.Failure` or just yield a failed `AssetCheckResult`.

#### Changes in `dlt_helpers.py`

Update `validate_with_pandera` to accept a `strict` flag:

```python
def validate_with_pandera(
    # ...
    strict: bool = False,
) -> bool:
    # ...
    # If strict=True and validation fails -> Raise Exception
    # If strict=False and validation fails -> Return False (log warning)
```

### 2. Remove Deprecated Code

Remove `add_phlo_timestamp` from `phlo.ingest.dlt.dlt_helpers`. All ingestion paths should now use `inject_metadata_columns` which handles `_phlo_row_id` generation and other metadata consistently.

## Consequences

### Positive

- **Better Data Quality**: Pipelines fail by default on invalid data, preventing "silent failures".
- **Cleaner Codebase**: Removed deprecated function reduces confusion.
- **User Control**: Explicit control over validation strictness.

### Negative

- **Potential Breakage**: Existing pipelines that rely on the implicit "warn-only" behavior will now fail if they have data quality issues (unless updated to `strict_validation=False`). This is an intentional breaking change to enforce quality.

### Risks

- Users upgrading `phlo` might experience sudden pipeline failures if their data was already failing validation silently. We should document this in the changelog.

## Verification Plan

### Automated Tests

1.  **Strict Validation Tests**:
    - Verify `phlo_ingestion(..., strict_validation=True)` raises Failure on invalid data.
    - Verify `phlo_ingestion(..., strict_validation=False)` does NOT raise Failure but reports failed check.
    - Verify `validate_with_pandera(..., strict=True)` raises Exception.
    - Verify `phlo_ingestion(..., strict_validation=True)` raises Failure on invalid data.
    - Verify `phlo_ingestion(..., strict_validation=False)` does NOT raise Failure but reports failed check.
    - Verify `validate_with_pandera(..., strict=True)` raises Exception.

2.  **Regression Tests**:
    - Ensure existing tests for `validate_with_pandera` pass (they verify datetime coercion logic).

## Beads

- phlo-nwk.4.3: Make ingestion validation failures actionable (configurable strictness) (complete)
- phlo-nwk.4.4: Remove deprecated add_phlo_timestamp path (complete)
