"""Pydantic models for Dagster asset output structures.

This module defines Pydantic models that specify the expected output schemas
for various pipeline stages. These models are used for:

1. **Validation**: Ensure asset materialization results have correct structure
2. **Metadata Tracking**: Track statistics and status of asset execution
3. **Type Safety**: Provide type hints for downstream consumers

Available Models:
    - **RawDataOutput**: Output model for raw data ingestion assets
    - **TablePublishStats**: Statistics for a published table
    - **PublishPostgresOutput**: Output model for Trino to Postgres publishing

Example:
    ```python
    from phlo_pandera.schemas import RawDataOutput, TablePublishStats

    # Create output from ingestion asset
    output = RawDataOutput(
        status="available",
        path="/data/raw/events",
        file_count=42,
        files=["part_001.parquet", "part_002.parquet"],
    )

    # Create stats for publishing
    stats = TablePublishStats(row_count=10000, column_count=15)
    ```

See Also:
    - Pydantic documentation for model validation
    - Dagster documentation for asset outputs

"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RawDataOutput(BaseModel):
    """Output model for raw data ingestion assets.

    Captures the status and metadata of raw data ingestion operations,
    including file counts and paths.

    Example:
        ```python
        # Successful ingestion
        output = RawDataOutput(
            status="available",
            path="s3://lakehouse/raw/events",
            file_count=5,
            files=["part_001.parquet", "part_002.parquet"],
        )

        # No data available
        output = RawDataOutput(
            status="no_data",
            path="s3://lakehouse/raw/events",
            file_count=0,
            files=[],
        )
        ```
    """

    status: str = Field(
        ...,
        description="Status of the raw data: 'available' or 'no_data'",
    )
    path: str = Field(
        ...,
        description="Path to the raw data directory",
    )
    file_count: int = Field(
        default=0,
        ge=0,
        description="Total number of parquet files found",
    )
    files: list[str] = Field(
        default_factory=list,
        description="List of file names (up to 10 for display)",
        max_length=10,
    )


class TablePublishStats(BaseModel):
    """Statistics for a published table.

    Captures row and column counts for tables published from Trino to Postgres
    or other destinations.

    Example:
        ```python
        stats = TablePublishStats(
            row_count=10000,
            column_count=15,
        )
        ```
    """

    row_count: int = Field(
        ...,
        ge=0,
        description="Number of rows in the published table",
    )
    column_count: int = Field(
        ...,
        ge=0,
        description="Number of columns in the published table",
    )


class PublishPostgresOutput(BaseModel):
    """Output model for Trino to Postgres publishing assets.

    Aggregates TablePublishStats for multiple tables published in a single
    operation.

    Example:
        ```python
        from phlo_pandera.schemas import PublishPostgresOutput, TablePublishStats

        output = PublishPostgresOutput(
            tables={
                "customers": TablePublishStats(row_count=5000, column_count=12),
                "orders": TablePublishStats(row_count=25000, column_count=8),
            }
        )
        ```
    """

    tables: dict[str, TablePublishStats] = Field(
        ...,
        description="Publishing statistics for each table",
    )
