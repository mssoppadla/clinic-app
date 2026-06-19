import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.models import Base

config = context.config
# Migrations run DDL, so they use the ADMIN/owner connection (superuser in prod). Falls back
# to APP_DATABASE_URL for CI/SQLite/single-role setups.
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get("APP_ADMIN_DATABASE_URL") or os.environ.get("APP_DATABASE_URL", "sqlite+pysqlite:///./local.db"),
)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
