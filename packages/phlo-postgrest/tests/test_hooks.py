"""Unit tests for PostgREST lifecycle hooks.

Verifies schema reload signaling after configuration changes, psql subprocess
failure and nonzero-exit logging, and that reloads happen only when the
service restart failed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_reload_schema_notifies_postgrest(monkeypatch, tmp_path):
    """Schema reload should use PostgREST's NOTIFY channel."""
    from phlo_postgrest import hooks

    config_dir = tmp_path / ".phlo" / "postgrest" / "conf"
    config_dir.mkdir(parents=True)
    (config_dir / "postgrest.conf").write_text(
        'db-uri = "postgresql://phlo:secret@postgres:5432/phlo"\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr(hooks.subprocess, "run", fake_run)

    hooks.reload_schema()

    assert captured["cmd"][:3] == ["docker", "exec", "-e"]
    assert "PGPASSWORD=secret" in captured["cmd"]
    assert "demo-postgres-1" in captured["cmd"]
    assert "NOTIFY pgrst, 'reload schema';" in captured["cmd"]


def test_run_psql_logs_subprocess_failures(monkeypatch):
    """Low-level subprocess failures should be logged before re-raising."""
    from phlo_postgrest import hooks

    captured = {}

    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")
    monkeypatch.setattr(
        hooks.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 30)),
    )
    monkeypatch.setattr(
        hooks.logger,
        "exception",
        lambda event, **kwargs: captured.update({"event": event, **kwargs}),
    )

    try:
        hooks._run_psql("postgresql://phlo:secret@postgres:5432/phlo", "SELECT 1;")
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("TimeoutExpired was not re-raised")

    assert captured["event"] == "postgrest_schema_reload_psql_failed"
    assert captured["postgres_container"] == "demo-postgres-1"
    assert captured["database"] == "phlo"
    assert captured["db_user"] == "phlo"


def test_run_psql_logs_nonzero_exits(monkeypatch):
    """Failed psql exits should log structured context before raising."""
    from phlo_postgrest import hooks

    captured = {}

    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")

    class Result:
        returncode = 1
        stderr = "first line\nsecond line\n"

    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(
        hooks.logger,
        "error",
        lambda event, **kwargs: captured.update({"event": event, **kwargs}),
    )

    try:
        hooks._run_psql("postgresql://phlo:secret@postgres:5432/phlo", "SELECT 1;")
    except RuntimeError as exc:
        assert str(exc) == "psql failed: first line\nsecond line"
    else:
        raise AssertionError("RuntimeError was not raised")

    assert captured["event"] == "postgrest_schema_reload_psql_failed"
    assert captured["postgres_container"] == "demo-postgres-1"
    assert captured["database"] == "phlo"
    assert captured["db_user"] == "phlo"
    assert captured["return_code"] == 1
    assert captured["stderr_line_count"] == 2


def test_configure_schemas_does_not_reload_after_successful_restart(monkeypatch, tmp_path):
    """A successful restart already applies config, so no extra NOTIFY is needed."""
    from phlo_postgrest import hooks

    config_dir = tmp_path / ".phlo" / "postgrest" / "conf"
    config_dir.mkdir(parents=True)
    (config_dir / "postgrest.conf").write_text(
        'db-uri = "postgresql://phlo:secret@postgres:5432/phlo"\n'
        'db-anon-role = "phlo"\n'
        'db-schemas = "public"\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: None)
    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")
    monkeypatch.setattr(hooks, "_wait_for_healthy", lambda *args, **kwargs: None)
    calls = {"reloads": 0}
    monkeypatch.setattr(
        hooks,
        "reload_schema",
        lambda: calls.update({"reloads": calls["reloads"] + 1}),
    )

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Result())

    hooks.configure_schemas()

    assert calls["reloads"] == 0


def test_configure_schemas_reloads_when_restart_fails(monkeypatch, tmp_path):
    """If the restart path fails, fall back to PostgREST's schema reload notification."""
    from phlo_postgrest import hooks

    config_dir = tmp_path / ".phlo" / "postgrest" / "conf"
    config_dir.mkdir(parents=True)
    (config_dir / "postgrest.conf").write_text(
        'db-uri = "postgresql://phlo:secret@postgres:5432/phlo"\n'
        'db-anon-role = "phlo"\n'
        'db-schemas = "public"\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: None)
    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")
    calls = {"reloads": 0}
    monkeypatch.setattr(
        hooks,
        "reload_schema",
        lambda: calls.update({"reloads": calls["reloads"] + 1}),
    )

    class Result:
        returncode = 1
        stderr = "restart failed"

    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Result())

    hooks.configure_schemas()

    assert calls["reloads"] == 1


def _write_config(tmp_path, anon_role: str) -> Path:
    config_dir = tmp_path / ".phlo" / "postgrest" / "conf"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "postgrest.conf"
    config_file.write_text(
        'db-uri = "postgresql://phlo:secret@postgres:5432/phlo"\n'
        f'db-anon-role = "{anon_role}"\n'
        'db-schemas = "crm,phlo,polaris_schema,public"\n'
    )
    return config_file


def _stub_restart(monkeypatch, hooks):
    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(hooks, "_resolve_container_name", lambda service: f"demo-{service}-1")
    monkeypatch.setattr(hooks, "_wait_for_healthy", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.subprocess, "run", lambda *args, **kwargs: Result())


def test_configure_schemas_replaces_the_owner_as_anonymous_role(monkeypatch, tmp_path):
    """Anonymous callers must not act as the database owner or see internal schemas."""
    from phlo_postgrest import hooks

    config_file = _write_config(tmp_path, anon_role="phlo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POSTGRES_MART_SCHEMA", raising=False)
    _stub_restart(monkeypatch, hooks)
    statements: list[str] = []
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: statements.append(sql))

    hooks.configure_schemas()

    config = hooks._read_config_values(config_file)
    assert config["db-anon-role"] == "anon"
    assert config["db-schemas"] == "marts,api"
    (sql,) = statements
    assert "CREATE ROLE anon NOLOGIN" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA marts TO anon;" in sql
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO anon;" in sql
    for forbidden in ("INSERT", "UPDATE", "DELETE", "public", "polaris_schema", "SCHEMA phlo"):
        assert forbidden not in sql


def test_configure_schemas_keeps_an_operator_chosen_anonymous_role(monkeypatch, tmp_path):
    """Hardened deployments can point db-anon-role at a role with no grants."""
    from phlo_postgrest import hooks

    config_file = _write_config(tmp_path, anon_role="web_nobody")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_MART_SCHEMA", "serving")
    _stub_restart(monkeypatch, hooks)
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: None)

    hooks.configure_schemas()

    config = hooks._read_config_values(config_file)
    assert config["db-anon-role"] == "web_nobody"
    assert config["db-schemas"] == "serving,api"


def test_configure_schemas_rejects_an_unsafe_mart_schema(monkeypatch, tmp_path):
    """The mart schema is interpolated into SQL, so only plain identifiers pass."""
    import pytest

    from phlo_postgrest import hooks

    _write_config(tmp_path, anon_role="phlo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_MART_SCHEMA", "marts; DROP SCHEMA phlo")
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: pytest.fail("ran SQL"))

    with pytest.raises(ValueError, match="POSTGRES_MART_SCHEMA"):
        hooks.configure_schemas()


def test_configure_schemas_runs_as_the_project_postgres_user(monkeypatch, tmp_path):
    """Default privileges follow the role that publishes marts: POSTGRES_USER."""
    from phlo_postgrest import hooks

    config_file = _write_config(tmp_path, anon_role="phlo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_USER", "lake")
    monkeypatch.setenv("POSTGRES_DB", "lakehouse")
    _stub_restart(monkeypatch, hooks)
    uris: list[str] = []
    monkeypatch.setattr(hooks, "_run_psql", lambda db_uri, sql: uris.append(db_uri))

    hooks.configure_schemas()

    assert uris == ["postgresql://lake@postgres/lakehouse"]
    assert hooks._read_config_values(config_file)["db-anon-role"] == "anon"
