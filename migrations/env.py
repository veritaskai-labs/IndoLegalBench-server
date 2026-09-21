"""Konfigurasi Alembic.

PENTING: setiap kali ada modul baru yang punya models.py berisi tabel,
tambahkan importnya di bagian "Import seluruh model" di bawah. Kalau
lupa, Alembic tidak akan melihat tabel itu dan autogenerate akan
menghasilkan migration yang salah (menganggap tabelnya harus dihapus).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import seluruh model supaya terdaftar di Base.metadata.
# Tambahkan baris baru di blok ini setiap kali ada modul baru bertabel.
#
# TODO(PBI-3): from app.modules.cases import models as cases_models
# TODO(PBI-10): from app.modules.providers import models as providers_models
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.suites import models as suites_models  # noqa: F401
from app.shared.config import get_settings
from app.shared.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL database diambil dari settings, bukan dari alembic.ini,
# supaya kredensial tidak pernah masuk ke repo.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
