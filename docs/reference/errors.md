# Errors

Phlo error codes are defined by `PhloErrorCode` in `src/phlo/exceptions.py`. The exception class is included in each message, together with suggestions and a documentation URL.

## PHLO-001

**Exception class:** `PhloDiscoveryError`

Phlo raises this code when Dagster cannot discover an asset.

- Check that the workflow module imports without an exception.
- Check installed provider packages and entry points.
- Check the workflow path and asset decorator configuration.

## PHLO-002

**Exception class:** `PhloError`

Phlo uses this code for a schema mismatch reported by a provider integration.

- Compare the declared schema with the destination schema.
- Check the unique key and required fields.
- Inspect the underlying exception included as the cause.

## PHLO-003

**Exception class:** `PhloError`

Phlo uses this code when a cron expression is invalid.

- Check the cron field count and value ranges.
- Check the schedule name and timezone.
- Test the expression with the orchestrator that owns execution.

## PHLO-004

**Exception class:** `PhloValidationError`

Phlo raises this code when data validation fails.

- Inspect the validation result for failing columns and checks.
- Compare incoming values with the Pandera contract.
- Check whether strict validation is enabled.

## PHLO-005

**Exception class:** `PhloConfigError`

Phlo raises this code when required decorator configuration is missing or invalid.

- Add `validation_schema` or `table_schema` where required.
- Check that `unique_key` exists in the validation schema.
- Check merge and partition options.

## PHLO-006

**Exception class:** `PhloIngestionError`

Phlo raises this code when an ingestion run fails.

- Check source connectivity and source credentials.
- Inspect the ingestion provider log and underlying cause.
- Check destination availability and write permissions.

## PHLO-007

**Exception class:** `PhloTableError`

Phlo raises this code when an Iceberg table operation targets a missing table.

- Check the catalog, namespace, and table name.
- Check the active Nessie reference.
- List catalog tables before retrying the operation.

## PHLO-008

**Exception class:** `PhloInfrastructureError` or `PhloCapabilitySetupError`

Phlo raises this code when infrastructure is unavailable or a capability cannot be set up.

- Check `phlo services status` and service health.
- Check capability provider installation and selection.
- Check endpoint, port, credentials, and container logs.

## PHLO-200

**Exception class:** `SchemaConversionError`

Phlo raises this code when a Pandera schema cannot be converted to PyIceberg.

- Check that every field has a supported type.
- Replace untyped or nested fields with supported representations.
- Inspect the conversion message for the failing field.

## PHLO-201

**Exception class:** `PhloError`

Phlo uses this code for a field type conversion failure.

- Check the field annotation and its nullability.
- Check decimal precision and scale where applicable.
- Replace unsupported extension or collection types.

## PHLO-300

**Exception class:** `DLTPipelineError`

Phlo raises this code when DLT pipeline execution fails.

- Inspect the DLT load trace and pipeline state.
- Check destination connectivity and schema evolution.
- Check pipeline configuration and source credentials.

## PHLO-301

**Exception class:** `PhloError`

Phlo uses this code for a DLT source failure before data reaches the pipeline.

- Check the source endpoint, file, or database.
- Check credentials and source rate limits.
- Check that the returned data has the expected shape.

## PHLO-400

**Exception class:** `IcebergCatalogError`

Phlo raises this code when an Iceberg catalog operation fails.

- Check Nessie availability and catalog URI.
- Check S3 or MinIO endpoint and credentials.
- Check the namespace and active reference.

## PHLO-401

**Exception class:** `PhloError`

Phlo uses this code when an Iceberg table operation fails.

- Check table metadata and the requested operation.
- Check schema compatibility and concurrent updates.
- Check catalog and object-store permissions.

## PHLO-402

**Exception class:** `PhloError`

Phlo uses this code when an Iceberg table write fails.

- Compare the input columns and types with the table schema.
- Check partition fields and values.
- Check object-store capacity, endpoint, and write permissions.
