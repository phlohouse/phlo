# Troubleshoot Phlo errors

Find the `PHLO-` code in the first error, then follow its diagnostic path. Preserve the full exception chain and provider logs before retrying.

## PHLO-001

**Symptom:** Dagster does not load an expected asset.

1. Import the workflow module directly and fix the first import exception.
2. Run `phlo plugin list` and confirm that the asset's provider package is installed.
3. Check the workflow path and decorator arguments.
4. Run `phlo doctor` and reload the Dagster code location.

Verify that the asset appears in the Dagster asset graph.

## PHLO-002

**Symptom:** A provider reports that the declared schema differs from the destination schema.

1. Compare required fields, types, and nullability on both schemas.
2. Check that the unique key exists and has the same type in both schemas.
3. Inspect the underlying provider exception for the first incompatible field.
4. Update the declaration or follow [Evolve a schema](evolve-a-schema.md) when the destination must change.

Verify that the schema comparison passes before rerunning the asset.

## PHLO-003

**Symptom:** Phlo rejects a schedule's cron expression.

1. Check the number of fields and each field's allowed range.
2. Confirm the intended timezone and schedule name.
3. Validate the expression with the orchestrator that owns execution.

Verify that Dagster loads the schedule and shows the expected next tick.

## PHLO-004

**Symptom:** A Pandera or quality rule rejects one or more rows.

1. Read the failed column, check, and sample values in the validation result.
2. Compare the source values with the Pandera contract.
3. Determine whether the source data is wrong or the reviewed contract needs to change.
4. Correct the data or contract. Do not disable strict validation to hide a blocking failure.

Verify the same partition against the corrected contract before publication.

## PHLO-005

**Symptom:** An ingestion decorator is missing required schema configuration or contains incompatible options.

1. Add `validation_schema` or `table_schema` where required.
2. Confirm that `unique_key` names a field in the validation schema.
3. Check merge, partition, and table options against the [Python API reference](../reference/python-api.md).

Verify that the workflow module imports and appears in the asset graph.

## PHLO-006

**Symptom:** An ingestion run fails after it starts.

1. Inspect the underlying cause and the ingestion provider log.
2. Test source connectivity and credentials from the same execution environment.
3. Check source rate limits, response shape, and the failed partition boundary.
4. Check destination availability, capacity, and write permissions.

Retry only the failed partition, then confirm the run and catalog snapshot.

## PHLO-007

**Symptom:** An Iceberg operation cannot find the requested table.

1. Run `phlo catalog tables` and confirm the namespace and table name.
2. Check the active Nessie reference.
3. Confirm that the upstream asset published the expected partition.

Verify the table with `phlo catalog describe <schema.table>` before retrying the operation.

## PHLO-008

**Symptom:** A service is unavailable or Phlo cannot initialise a required capability.

1. Run `phlo services status` and `phlo doctor`.
2. Check that the selected capability provider is installed and configured.
3. Inspect the affected service's endpoint, port, credentials, health check, and container log.
4. Correct the first failed dependency before restarting the affected service.

Verify that doctor reports no failure for the capability or service.

## PHLO-200

**Symptom:** Phlo cannot convert a Pandera schema to PyIceberg.

1. Read the failing field name and source type from the exception.
2. Check that every field has a supported type and explicit nullability.
3. Replace unsupported nested, extension, or untyped fields with supported representations.

Run `phlo validate-schema <schema-file>` and verify that conversion succeeds.

## PHLO-201

**Symptom:** One field type cannot be converted.

1. Check the field annotation and nullability.
2. For decimal fields, check precision and scale.
3. Replace unsupported collection or extension types.

Verify the complete schema rather than testing only the corrected field.

## PHLO-300

**Symptom:** A dlt pipeline fails while normalising or loading data.

1. Inspect the dlt load trace and pipeline state.
2. Check destination connectivity, credentials, and capacity.
3. Compare schema-evolution output with the current destination schema.
4. Check the pipeline name, dataset, and destination configuration.

Retry the failed partition and confirm that dlt records a completed load package.

## PHLO-301

**Symptom:** A dlt source fails before records enter the pipeline.

1. Test the source endpoint, file, or database from the run environment.
2. Check credentials, permissions, pagination, and rate limits.
3. Capture a bounded sample and compare its shape with the source resource.

Verify source extraction with the same partition or cursor before rerunning the pipeline.

## PHLO-400

**Symptom:** An Iceberg catalog request fails.

1. Check Nessie availability and the configured catalog URI.
2. Check the object-store endpoint and credentials.
3. Confirm the namespace and active reference.
4. Inspect Nessie and object-store logs for the same request time.

Verify that `phlo catalog tables` succeeds before retrying the original operation.

## PHLO-401

**Symptom:** An operation on an existing Iceberg table fails.

1. Inspect the table metadata and requested operation.
2. Check schema compatibility and concurrent catalog updates.
3. Check catalog and object-store permissions.

Verify the table history and current snapshot before retrying.

## PHLO-402

**Symptom:** Iceberg rejects a table write.

1. Compare input columns, types, and nullability with the table schema.
2. Check partition fields and values.
3. Check object-store capacity, endpoint, and write permissions.
4. Confirm that another writer did not change the expected snapshot.

Retry only after the input schema and current snapshot match the write plan. Confirm the new snapshot in catalog history.
