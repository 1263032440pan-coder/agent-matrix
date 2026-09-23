"""F2 (RH-02 复验遗留): lifespan migration check must BLOCK startup when the
DB schema is behind alembic head — not just warn."""

import pytest

from apps.runtime.main import SchemaBehindError, _check_migration_version


@pytest.mark.asyncio
async def test_missing_alembic_version_blocks(monkeypatch, tmp_path):
    """No alembic_version table (unmigrated DB) → SchemaBehindError, not a warning."""
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/empty.db")
    monkeypatch.setattr("apps.runtime.main.engine", test_engine)

    # version files exist in repo (glob found) but DB has no alembic_version table
    with pytest.raises(SchemaBehindError, match="alembic upgrade head"):
        await _check_migration_version()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_behind_version_blocks(monkeypatch, tmp_path):
    """alembic_version present but older than head → SchemaBehindError."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    async with test_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('000000000000')"))

    monkeypatch.setattr("apps.runtime.main.engine", test_engine)

    with pytest.raises(SchemaBehindError, match="DB schema behind"):
        await _check_migration_version()
    await test_engine.dispose()
