"""Alembic-Environment (PLAT-001). Nutzt `config.POSTGRES_DSN` statt eines fest
eingetragenen `alembic.ini`-Werts, damit lokale/Staging/CI dieselbe .env-basierte
Konfiguration wie der Rest des Bots verwenden.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from stockbot import config as app_config

alembic_config = context.config
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# `attributes["sqlalchemy.url"]` ist der übliche Alembic-Weg, eine URL programmatisch zu
# injizieren (z. B. aus Tests) — ohne das hätte die App-Config (.env) immer Vorrang.
alembic_config.set_main_option(
    "sqlalchemy.url", alembic_config.attributes.get("sqlalchemy.url", app_config.POSTGRES_DSN)
)

# Kein ORM-Modell-Metadata-Objekt (folgt mit den Domänenobjekten, nächster Checklisten-Punkt) —
# Migrationen definieren das Zielschema bislang explizit über `op.create_table` (siehe
# migrations/versions/0001_initial_schema.py), gespiegelt aus docs/DB_SCHEMA_SQLITE.md.
target_metadata = None


def run_migrations_offline() -> None:
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
