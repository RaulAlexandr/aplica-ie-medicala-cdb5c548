from logging.config import fileConfig
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base
import app.database  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction(): context.run_migrations()


async def run_async() -> None:
    connectable = async_engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=None)
    async with connectable.connect() as connection:
        await connection.run_sync(lambda sync_conn: context.configure(connection=sync_conn, target_metadata=target_metadata))
        async with connection.begin():
            await connection.run_sync(lambda sync_conn: context.run_migrations())
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async())


if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
