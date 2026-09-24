"""Alembic environment.

Rule 6: Alembic owns our tables in `public` and must leave every
Supabase-managed schema alone.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models registers them on Base.metadata for autogenerate.
# `app.tables` is the one place that stays complete as features add tables.
import app.tables  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Schemas Supabase creates and manages. Autogenerate must never touch them.
SUPABASE_SCHEMAS = {
    "auth",
    "storage",
    "realtime",
    "_realtime",
    "graphql",
    "graphql_public",
    "extensions",
    "vault",
    "supabase_functions",
    "supabase_migrations",
    "pgbouncer",
    "net",
    "cron",
    "pgsodium",
    "pgsodium_masks",
    "information_schema",
    "pg_catalog",
    "pg_toast",
}


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name is None or name == "public"
    return True


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    schema = getattr(obj, "schema", None)
    if schema in SUPABASE_SCHEMAS:
        return False
    # Alembic's own bookkeeping table is not part of our model; without this,
    # autogenerate helpfully offers to drop it.
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=False,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
        version_table_schema="public",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
