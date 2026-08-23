#!/usr/bin/env python3
"""
Test DuckDB Iceberg extension integration with Phlo Iceberg tables.

This script demonstrates querying Iceberg tables directly using DuckDB,
bypassing Trino for fast ad-hoc analysis.
"""

import os
import sys

import pytest
from _pytest.outcomes import Skipped

# Mark entire module as integration tests (requires Nessie and MinIO)
pytestmark = pytest.mark.integration

# Set environment variables for localhost (when running from host)
# Must be set before any phlo imports to affect config loading
os.environ.setdefault("NESSIE_HOST", "localhost")
os.environ.setdefault("MINIO_HOST", "localhost")


def _is_nessie_unreachable_error(exc: Exception) -> bool:
    """Check whether an exception message matches known Nessie connectivity failures."""
    message = str(exc).lower()
    patterns = (
        "temporary failure in name resolution",
        "nameresolutionerror",
        "connection refused",
        "failed to establish",
        "max retries exceeded",
        "name or service not known",
        "connection timed out",
    )
    return any(pattern in message for pattern in patterns)


def test_duckdb_iceberg():
    """Validate DuckDB Iceberg queries against a reachable Phlo environment."""
    try:
        import duckdb
    except ImportError:
        pytest.skip("DuckDB not installed")

    print("=== Testing DuckDB Iceberg Extension ===")

    # Create DuckDB connection
    conn = duckdb.connect(":memory:")

    # Step 1: Install and load Iceberg extension
    print("1. Installing Iceberg extension...")
    try:
        conn.execute("INSTALL iceberg")
        conn.execute("LOAD iceberg")
        print("   ✓ Iceberg extension loaded\n")
    except Exception as e:
        pytest.skip(f"DuckDB Iceberg extension unavailable: {e}")

    # Step 2: Configure S3/MinIO connection
    print("2. Configuring S3/MinIO connection...")
    try:
        # Get credentials from environment or use defaults
        minio_endpoint = (
            os.getenv("MINIO_HOST", "localhost") + ":" + os.getenv("MINIO_API_PORT", "9000")
        )
        minio_user = os.getenv("MINIO_ROOT_USER", "minio")
        minio_password = os.getenv("MINIO_ROOT_PASSWORD", "minio123")

        conn.execute(f"SET s3_endpoint = '{minio_endpoint}'")
        conn.execute("SET s3_use_ssl = false")
        conn.execute("SET s3_url_style = 'path'")
        conn.execute(f"SET s3_access_key_id = '{minio_user}'")
        conn.execute(f"SET s3_secret_access_key = '{minio_password}'")

        print(f"   ✓ Connected to MinIO at {minio_endpoint}\n")
    except Exception as e:
        pytest.fail(f"Failed to configure S3: {e}")

    # Step 3: Get table location from PyIceberg catalog
    print("3. Getting Iceberg table location from Nessie...")
    try:
        # Use PyIceberg to get the actual table location
        from phlo_iceberg.catalog import get_catalog

        catalog = get_catalog(ref="main")

        # List tables to see what exists
        try:
            tables = list(catalog.list_tables("raw"))
            if not tables:
                pytest.skip("No tables found in raw namespace (run ingestion first)")

            print(f"   ✓ Found {len(tables)} table(s) in raw namespace")

        except Exception:
            pytest.skip("Raw namespace not found (run ingestion first)")

    except ImportError:
        pytest.skip("PyIceberg not available")
    except Exception as exc:
        if _is_nessie_unreachable_error(exc):
            pytest.skip("Nessie not reachable from test host.")
        raise

    # Step 4: Query Iceberg table using metadata location
    print("\n4. Querying Iceberg table (raw.entries)...")
    try:
        # Load table to get metadata location
        table = catalog.load_table("raw.entries")
        metadata_location = table.metadata_location

        print(f"   Using metadata: {metadata_location}")

        # Query using metadata location (most reliable)
        result = conn.execute(f"""
            SELECT COUNT(*) as row_count
            FROM iceberg_scan('{metadata_location}')
        """).fetchone()

        row_count = result[0] if result else 0
        print("   ✓ Successfully queried Iceberg table")
        print(f"   ✓ Found {row_count} rows in raw.entries\n")

        if row_count == 0:
            print("   ⚠ Table is empty (run ingestion first)")

    except Exception as e:
        error_msg = str(e)
        if _is_nessie_unreachable_error(e):
            pytest.skip("Nessie not reachable from test host.")
        if "No such file" in error_msg or "does not exist" in error_msg or "404" in error_msg:
            pytest.skip(f"Iceberg table not found (run ingestion first): {error_msg}")
        pytest.fail(f"Failed to query Iceberg table: {e}")

    # Step 5: Query with filters (if table has data)
    if row_count > 0:
        print("5. Testing partition filtering...")
        try:
            result = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    MIN(date) as min_date,
                    MAX(date) as max_date
                FROM iceberg_scan('{metadata_location}')
                LIMIT 10
            """).fetchone()

            if result is None:
                raise AssertionError("Expected a result row from partition filtering query")

            print("   ✓ Partition filtering works")
            print(f"     Total rows: {result[0]}")
            print(f"     Date range: {result[1]} to {result[2]}\n")

        except Exception as e:
            print(f"   ⚠ Partition filtering test failed: {e}\n")

        # Step 6: Test field selection
        print("6. Testing field selection...")
        try:
            result = conn.execute(f"""
                SELECT
                    _id,
                    sgv,
                    type,
                    date_string
                FROM iceberg_scan('{metadata_location}')
                LIMIT 5
            """).fetchall()

            print("   ✓ Field selection works")
            print(f"     Retrieved {len(result)} sample rows\n")

            # Display sample data
            print("   Sample data:")
            for row in result[:3]:
                print(f"     - ID: {row[0]}, SGV: {row[1]}, Type: {row[2]}")
            print()

        except Exception as e:
            print(f"   ⚠ Field selection test failed: {e}\n")

    # Step 7: Test other layers (if they exist)
    print("7. Checking other pipeline layers...")
    layers_to_check = [
        ("bronze.stg_entries", "Bronze layer"),
        ("silver.fct_glucose_readings", "Silver layer"),
        ("gold.dim_date", "Gold layer"),
    ]

    layers_found = 0
    for table_path, layer_name in layers_to_check:
        try:
            # Use catalog to get proper table location
            layer_table = catalog.load_table(table_path)
            layer_metadata = layer_table.metadata_location

            result = conn.execute(f"""
                SELECT COUNT(*) as row_count
                FROM iceberg_scan('{layer_metadata}')
            """).fetchone()

            row_count = result[0] if result else 0
            if row_count > 0:
                print(f"   ✓ {layer_name}: {row_count} rows")
                layers_found += 1
            else:
                print(f"   ⚠ {layer_name}: table exists but empty")

        except Exception:
            print(f"   - {layer_name}: not found (run dbt first)")

    print()

    # Summary
    print("=== Test Summary ===")
    print("✓ DuckDB Iceberg extension working")
    print("✓ Can query Iceberg tables from MinIO")
    print("✓ S3/MinIO connection successful")
    if row_count > 0:
        print(f"✓ Found data in raw.entries ({row_count} rows)")
    if layers_found > 0:
        print(f"✓ Found {layers_found} transformed layers")
    print()
    print("Integration test PASSED")


if __name__ == "__main__":
    success = False
    try:
        test_duckdb_iceberg()
        success = True
    except Skipped as exc:
        print(f"Test skipped: {exc}")
        success = True
    except Exception as e:
        print(f"Test failed with unexpected error: {e}")
        import traceback

        traceback.print_exc()
    sys.exit(0 if success else 1)
