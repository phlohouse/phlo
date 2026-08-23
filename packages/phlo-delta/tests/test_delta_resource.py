"""Unit tests for DeltaResource table-store interface compatibility.

Delta Lake supports only identity partition transforms and no refs, so
the resource must translate identity specs to partition columns, accept
override_ref="main" for interface parity, and fail fast with
PhloConfigError on anything else.
"""

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from phlo.exceptions import PhloConfigError
from phlo_delta.resource import DeltaResource


def test_delta_resource_support_is_main_ref_identity_partitioned() -> None:
    support = DeltaResource().support

    assert support.supports_refs is False
    assert support.partition_transforms == frozenset({"identity"})
    assert support.supports_snapshots is True
    assert support.supports_compaction is True
    assert support.supports_vacuum is True


def test_delta_resource_ensure_table_maps_identity_partition_spec() -> None:
    """DeltaResource should translate identity partition specs into Delta columns."""
    resource = DeltaResource()
    schema = pa.schema([pa.field("partition_date", pa.string())])
    mock_table = MagicMock()

    with patch("phlo_delta.resource.ensure_table", return_value=mock_table) as mock_ensure_table:
        result = resource.ensure_table(
            table_name="raw.pokemon_species",
            schema=schema,
            partition_spec=[("partition_date", "identity")],
            override_ref="main",
        )

    mock_ensure_table.assert_called_once_with(
        table_name="raw.pokemon_species",
        schema=schema,
        partition_columns=["partition_date"],
    )
    assert result == mock_table


def test_delta_resource_ensure_table_rejects_transform_partition_spec() -> None:
    """DeltaResource should fail fast on non-identity partition transforms."""
    resource = DeltaResource()
    schema = pa.schema([pa.field("event_time", pa.timestamp("us"))])

    with pytest.raises(PhloConfigError, match="only supports identity partition transforms"):
        resource.ensure_table(
            table_name="raw.events",
            schema=schema,
            partition_spec=[("event_time", "day")],
        )


def test_delta_resource_append_parquet_allows_main_override_ref() -> None:
    """DeltaResource should accept the default main ref for interface compatibility."""
    resource = DeltaResource()

    with patch(
        "phlo_delta.resource.append_to_table", return_value={"rows_inserted": 3, "rows_deleted": 0}
    ) as mock_append:
        result = resource.append_parquet(
            table_name="raw.pokemon_species",
            data_path="/tmp/pokemon.parquet",
            override_ref="main",
        )

    mock_append.assert_called_once_with(
        table_name="raw.pokemon_species",
        data_path="/tmp/pokemon.parquet",
    )
    assert result == {"rows_inserted": 3, "rows_deleted": 0}


def test_delta_resource_merge_parquet_rejects_non_main_override_ref() -> None:
    """DeltaResource should reject branch-like override refs it cannot honor."""
    resource = DeltaResource()

    with pytest.raises(PhloConfigError, match="does not support refs; got override_ref='dev'"):
        resource.merge_parquet(
            table_name="raw.pokemon_species",
            data_path="/tmp/pokemon.parquet",
            unique_key="pokemon_id",
            override_ref="dev",
        )


def test_delta_ref_validation_error_mentions_support_metadata() -> None:
    with pytest.raises(PhloConfigError) as exc:
        DeltaResource().merge_parquet(
            table_name="raw.events",
            data_path="/tmp/events.parquet",
            unique_key="id",
            override_ref="dev",
        )

    assert "does not support refs" in str(exc.value)
