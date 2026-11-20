import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import config as app_config
from app import models  # noqa: F401 — jadvallar metadata'ga yozilishi uchun
from app.db import Base

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=app_config.DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(app_config.DATABASE_URL)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
