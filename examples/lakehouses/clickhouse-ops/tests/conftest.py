"""Pin the dbt query-engine settings before any framework import.

phlo-dbt regenerates ``profiles.yml`` from ``DbtSettings`` whenever its hooks
run, so the test session pins the ClickHouse routing up front. Without this,
any capability discovery triggered mid-session would regenerate a default
trino profile and clobber the checked-in ClickHouse one.
"""

from __future__ import annotations

import os

_DEFAULTS = {
    "DBT_QUERY_ENGINE_TYPE": "clickhouse",
    "DBT_QUERY_HOST": "clickhouse",
    "DBT_QUERY_PORT": "8123",
    "DBT_QUERY_USER": "default",
    "DBT_QUERY_SCHEMA": "marts",
    "DBT_PROJECT_DIR": "workflows/operational_marts/dbt",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)
