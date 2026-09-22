"""Koneksi database dan base class untuk seluruh model SQLAlchemy.

Semua modul memakai Base dan get_db dari sini. Jangan membuat engine
atau session baru di dalam modul.

Engine sengaja dibuat malas (lazy) lewat get_engine(), bukan langsung
saat file ini diimpor. Alasannya: mengimpor app.main jadi tidak butuh
koneksi atau driver database, sehingga test dan pembuatan kontrak
OpenAPI bisa jalan tanpa PostgreSQL yang menyala.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.shared.config import get_settings


class Base(DeclarativeBase):
    """Base class untuk seluruh model.

    Semua models.py di tiap modul harus mewarisi dari sini, supaya
    Alembic bisa mendeteksi seluruh tabel lewat satu metadata.
    """


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()

    # Dua driver, dua kebutuhan berbeda, dan keduanya tidak saling
    # menggantikan.
    #
    # connect_timeout: tanpa batas waktu, percobaan koneksi ke database
    # yang mati baru menyerah setelah beberapa menit, dan test health
    # ikut menggantung selama itu. Hanya dikenal driver PostgreSQL.
    #
    # check_same_thread: khusus SQLite, dipakai saat smoke test lokal
    # dijalankan tanpa PostgreSQL yang menyala.
    connect_args: dict[str, int | bool] = {}
    if settings.database_url.startswith("postgresql"):
        connect_args["connect_timeout"] = 3
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.debug and settings.app_env == "local",
        connect_args=connect_args,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Dependency FastAPI untuk mendapatkan session database.

    Dipakai begini di router:

        @router.get("/")
        def handler(db: Session = Depends(get_db)):
            ...
    """
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
