# Error reference

`PhloErrorCode` in `src/phlo/exceptions.py` defines these stable error codes. A raised `PhloError` includes the code, message, suggestions, documentation URL, and underlying cause when one exists.

| Code | Symbol | Meaning | Troubleshooting |
| --- | --- | --- | --- |
| `PHLO-001` | `ASSET_NOT_DISCOVERED` | Dagster could not discover an asset. | [Diagnose PHLO-001](../guides/troubleshoot-errors.md#phlo-001) |
| `PHLO-002` | `SCHEMA_MISMATCH` | A declared schema does not match the provider or destination schema. | [Diagnose PHLO-002](../guides/troubleshoot-errors.md#phlo-002) |
| `PHLO-003` | `INVALID_CRON` | A schedule contains an invalid cron expression. | [Diagnose PHLO-003](../guides/troubleshoot-errors.md#phlo-003) |
| `PHLO-004` | `VALIDATION_FAILED` | Data failed a validation rule. | [Diagnose PHLO-004](../guides/troubleshoot-errors.md#phlo-004) |
| `PHLO-005` | `MISSING_SCHEMA` | Required decorator schema configuration is missing or invalid. | [Diagnose PHLO-005](../guides/troubleshoot-errors.md#phlo-005) |
| `PHLO-006` | `INGESTION_FAILED` | An ingestion run failed. | [Diagnose PHLO-006](../guides/troubleshoot-errors.md#phlo-006) |
| `PHLO-007` | `TABLE_NOT_FOUND` | An Iceberg operation could not find its table. | [Diagnose PHLO-007](../guides/troubleshoot-errors.md#phlo-007) |
| `PHLO-008` | `INFRASTRUCTURE_ERROR` | Infrastructure is unavailable or a capability could not be set up. | [Diagnose PHLO-008](../guides/troubleshoot-errors.md#phlo-008) |
| `PHLO-200` | `SCHEMA_CONVERSION_ERROR` | A Pandera schema could not be converted to PyIceberg. | [Diagnose PHLO-200](../guides/troubleshoot-errors.md#phlo-200) |
| `PHLO-201` | `TYPE_CONVERSION_ERROR` | A field type could not be converted. | [Diagnose PHLO-201](../guides/troubleshoot-errors.md#phlo-201) |
| `PHLO-300` | `DLT_PIPELINE_FAILED` | A dlt pipeline failed during execution. | [Diagnose PHLO-300](../guides/troubleshoot-errors.md#phlo-300) |
| `PHLO-301` | `DLT_SOURCE_ERROR` | A dlt source failed before data reached the pipeline. | [Diagnose PHLO-301](../guides/troubleshoot-errors.md#phlo-301) |
| `PHLO-400` | `ICEBERG_CATALOG_ERROR` | An Iceberg catalog operation failed. | [Diagnose PHLO-400](../guides/troubleshoot-errors.md#phlo-400) |
| `PHLO-401` | `ICEBERG_TABLE_ERROR` | An Iceberg table operation failed. | [Diagnose PHLO-401](../guides/troubleshoot-errors.md#phlo-401) |
| `PHLO-402` | `ICEBERG_WRITE_ERROR` | An Iceberg table write failed. | [Diagnose PHLO-402](../guides/troubleshoot-errors.md#phlo-402) |
