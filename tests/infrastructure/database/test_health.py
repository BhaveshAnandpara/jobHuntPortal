"""Health-check tests: success and failure paths, against real SQLite for
the success case and a mocked failing connection for the failure case (per
the task brief: health-check logic is tested against a mocked/fake
connection, not a live database).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from infrastructure.database.health import check_connection, check_connection_async


def test_check_connection_true_on_working_sqlite_engine() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        assert check_connection(engine) is True
    finally:
        engine.dispose()


def test_check_connection_false_when_connect_raises_sqlalchemy_error() -> None:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    assert check_connection(engine) is False


def test_check_connection_false_when_execute_raises_sqlalchemy_error() -> None:
    engine = MagicMock()
    connection = MagicMock()
    connection.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    engine.connect.return_value.__enter__.return_value = connection
    assert check_connection(engine) is False


def test_check_connection_does_not_swallow_non_sqlalchemy_errors() -> None:
    # Only SQLAlchemyError is normalized to False; anything else (a bug in
    # this module, an unrelated exception) should still surface.
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("not a db error")
    with pytest.raises(RuntimeError):
        check_connection(engine)


@pytest.mark.asyncio
async def test_check_connection_async_true_when_execute_succeeds() -> None:
    connection = AsyncMock()
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    engine.connect.return_value.__aexit__.return_value = False

    assert await check_connection_async(engine) is True
    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_connection_async_false_on_sqlalchemy_error() -> None:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("down"))

    assert await check_connection_async(engine) is False


@pytest.mark.asyncio
async def test_check_connection_async_false_when_execute_raises() -> None:
    connection = AsyncMock()
    connection.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    engine.connect.return_value.__aexit__.return_value = False

    assert await check_connection_async(engine) is False
