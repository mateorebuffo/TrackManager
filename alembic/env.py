from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Load app models so Alembic can detect schema changes
from app.db import Base  # noqa: F401
from app.models import source_track, normalized_track, review_item  # noqa: F401
from app.models import app_event, track_history, user_report  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override URL from app config so Railway's DATABASE_URL is always used
from app.config import settings as _app_settings
config.set_main_option("sqlalchemy.url", _app_settings.database_url_safe)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
