"""Tests PostgresResource lifecycle: transactions, health checks, query
helpers, and pool teardown under failure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_postgres.resource import PostgresResource


def _resource() -> PostgresResource:
    """Build a Postgres resource configured for local unit tests."""
    return PostgresResource(
        host="localhost",
        port=5432,
        user="phlo",
        password="secret",
        database="phlo",
    )


def _mock_pool(connection: MagicMock) -> MagicMock:
    """Create a mock SimpleConnectionPool that returns *connection*."""
    mock = MagicMock()
    mock.closed = False
    mock.getconn.return_value = connection
    return mock


def test_context_manager_rolls_back_and_closes_on_error() -> None:
    """Verify context manager rolls back, returns conn, and closes pool on exception."""
    connection = MagicMock()
    connection.closed = 0
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        with pytest.raises(RuntimeError, match="boom"):
            with _resource():
                raise RuntimeError("boom")

    connection.rollback.assert_called_once()
    mock_pool.putconn.assert_called_once_with(connection)
    mock_pool.closeall.assert_called_once()


def test_transactional_cursor_commits_on_success() -> None:
    """Verify transactional cursor commits and closes cursor on success."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        with resource.transactional_cursor() as current_cursor:
            assert current_cursor is cursor
            current_cursor.execute("SELECT 1")

    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    cursor.close.assert_called_once()


def test_transactional_cursor_rolls_back_on_error() -> None:
    """Verify transactional cursor rolls back and closes cursor on failure."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        with pytest.raises(ValueError, match="fail"):
            with resource.transactional_cursor():
                raise ValueError("fail")

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    cursor.close.assert_called_once()


def test_is_healthy_returns_true_on_success() -> None:
    """Verify is_healthy returns True when SELECT 1 succeeds."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        assert resource.is_healthy() is True


def test_is_healthy_returns_false_on_failure() -> None:
    """Verify is_healthy returns False when connection fails."""
    mock_pool = MagicMock()
    mock_pool.closed = False
    mock_pool.getconn.side_effect = Exception("connection refused")

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        assert resource.is_healthy() is False


def test_execute_commits() -> None:
    """Verify execute runs statement and commits."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        resource.execute("INSERT INTO t VALUES (%s)", (1,))

    cursor.execute.assert_called_once_with("INSERT INTO t VALUES (%s)", (1,))
    connection.commit.assert_called_once()


def test_query_returns_rows() -> None:
    """Verify query returns all rows from fetchall."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    cursor.fetchall.return_value = [(1,), (2,)]
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        result = resource.query("SELECT id FROM t")

    assert result == [(1,), (2,)]


def test_query_one_returns_first_row() -> None:
    """Verify query_one returns the first row from fetchone."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        result = resource.query_one("SELECT id FROM t LIMIT 1")

    assert result == (42,)


def test_query_one_returns_none_when_empty() -> None:
    """Verify query_one returns None when no rows exist."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        result = resource.query_one("SELECT id FROM t WHERE 1=0")

    assert result is None


def test_ensure_schema_executes_ddl() -> None:
    """Verify ensure_schema creates schema via transactional cursor."""
    connection = MagicMock()
    connection.closed = 0
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        resource.ensure_schema("my_schema")

    cursor.execute.assert_called_once()
    connection.commit.assert_called_once()


def test_close_pool_tears_down_pool() -> None:
    """Verify close_pool calls closeall on the pool."""
    connection = MagicMock()
    connection.closed = 0
    mock_pool = _mock_pool(connection)

    with patch("phlo_postgres.resource.pool.SimpleConnectionPool", return_value=mock_pool):
        resource = _resource()
        resource._ensure_connection()
        resource.close()
        resource.close_pool()

    mock_pool.putconn.assert_called_once_with(connection)
    mock_pool.closeall.assert_called_once()


def test_exit_closes_pool_even_when_close_raises() -> None:
    """Verify __exit__ always tears down the pool."""
    resource = _resource()
    resource.close = MagicMock(side_effect=RuntimeError("putconn failed"))  # type: ignore[method-assign]
    resource.close_pool = MagicMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="putconn failed"):
        resource.__exit__(None, None, None)

    resource.close_pool.assert_called_once()


def test_del_closes_pool_even_when_close_raises() -> None:
    """Verify __del__ still closes the pool when close fails."""
    resource = _resource()
    resource.close = MagicMock(side_effect=RuntimeError("putconn failed"))  # type: ignore[method-assign]
    resource.close_pool = MagicMock()  # type: ignore[method-assign]

    resource.__del__()

    resource.close_pool.assert_called_once()
