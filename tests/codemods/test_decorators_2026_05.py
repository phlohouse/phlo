"""Tests for decorators 2026-05 migration codemods.

Rewrites phlo_dlt/phlo_quality decorator imports into their provider-neutral
phlo.ingest.dlt and phlo.quality.pandera forms, handling aliases, callable
aliases, and aliased phlo namespace imports, and reports unchanged sources
without touching them.
"""

from __future__ import annotations

from phlo.codemods.decorators_2026_05 import migrate_decorators_2026_05_source


def test_migrates_phlo_dlt_decorator_import_to_provider_neutral_ingest() -> None:
    """DLT decorator imports should become phlo.ingest.dlt calls."""
    source = """from phlo_dlt import phlo_ingestion


@phlo_ingestion(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert (
        migrated.code
        == """import phlo


@phlo.ingest.dlt(table_name="events")
def events():
    pass
"""
    )


def test_migrates_phlo_dlt_decorator_module_import() -> None:
    """phlo_dlt.decorator imports should migrate the same way as package imports."""
    source = """from phlo_dlt.decorator import phlo_ingestion


@phlo_ingestion(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert "@phlo.ingest.dlt(" in migrated.code
    assert "from phlo_dlt.decorator import phlo_ingestion" not in migrated.code


def test_migrates_aliased_phlo_dlt_decorator_import() -> None:
    """Aliased DLT decorator imports should migrate to the explicit decorators 2026-05."""
    source = """from phlo_dlt import phlo_ingestion as ingest


@ingest(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert '@phlo.ingest.dlt(table_name="events")' in migrated.code
    assert "phlo_ingestion" not in migrated.code


def test_migrates_callable_phlo_ingestion_alias() -> None:
    """Existing phlo.ingestion decorator calls should become explicit DLT provider calls."""
    source = """import phlo


@phlo.ingestion(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert '@phlo.ingest.dlt(table_name="events")' in migrated.code


def test_adds_phlo_import_when_existing_phlo_import_is_aliased() -> None:
    """Aliased phlo imports should not satisfy the emitted phlo namespace."""
    source = """import phlo as p


@phlo.ingestion(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert "import phlo\nimport phlo as p" in migrated.code
    assert '@phlo.ingest.dlt(table_name="events")' in migrated.code


def test_migrates_aliased_phlo_quality_import() -> None:
    """Aliased quality decorator imports should migrate while preserving checks."""
    source = """from phlo.quality import NullCheck, phlo_quality as quality


@quality(table="bronze.events", checks=[NullCheck(columns=["id"])])
def events_quality():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert "from phlo.quality import NullCheck" in migrated.code
    assert "@phlo.quality.pandera(" in migrated.code
    assert "phlo_quality" not in migrated.code


def test_migrates_phlo_quality_import_while_preserving_other_quality_imports() -> None:
    """phlo_quality should become phlo.quality.pandera without dropping native checks."""
    source = """from phlo.quality import NullCheck, phlo_quality, RangeCheck


@phlo_quality(table="bronze.events", checks=[NullCheck(columns=["id"])])
def events_quality():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is True
    assert "import phlo" in migrated.code
    assert "from phlo.quality import NullCheck, RangeCheck" in migrated.code
    assert "@phlo.quality.pandera(" in migrated.code
    assert "phlo_quality" not in migrated.code


def test_reports_unchanged_source() -> None:
    """Already migrated modules should report no change."""
    source = """import phlo


@phlo.ingest.dlt(table_name="events")
def events():
    pass
"""

    migrated = migrate_decorators_2026_05_source(source)

    assert migrated.changed is False
    assert migrated.code == source
