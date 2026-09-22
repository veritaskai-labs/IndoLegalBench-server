"""Unit test pembuat session database di app/shared/database.py.

PBI-1, sub task [QA] SCRUM-96. Test lain selalu meng-override get_db,
jadi get_session_factory dan get_db sendiri tidak pernah dijalankan.
Di sini keduanya diuji dengan pengganti (stub): engine SQLite in-memory
menggantikan PostgreSQL, dan sesi palsu dipakai untuk memastikan sesi
selalu ditutup.
"""

import pytest
from sqlalchemy import create_engine

from app.shared import database


@pytest.fixture
def engine_sqlite(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    # get_session_factory di-cache. Dikosongkan sebelum dan sesudah
    # supaya factory yang terikat ke engine palsu tidak bocor ke test lain.
    database.get_session_factory.cache_clear()
    yield engine
    database.get_session_factory.cache_clear()
    engine.dispose()


def test_session_factory_terikat_ke_engine_aplikasi(engine_sqlite):
    sesi = database.get_session_factory()()
    try:
        assert sesi.get_bind() is engine_sqlite
        assert sesi.autoflush is False
    finally:
        sesi.close()


def test_session_factory_dibuat_sekali_lalu_dipakai_ulang(engine_sqlite):
    assert database.get_session_factory() is database.get_session_factory()


class SesiPalsu:
    def __init__(self) -> None:
        self.ditutup = False

    def close(self) -> None:
        self.ditutup = True


@pytest.fixture
def sesi_palsu(monkeypatch) -> SesiPalsu:
    sesi = SesiPalsu()
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: sesi)
    return sesi


def test_get_db_memberikan_sesi_lalu_menutupnya(sesi_palsu):
    generator = database.get_db()

    assert next(generator) is sesi_palsu
    assert sesi_palsu.ditutup is False

    with pytest.raises(StopIteration):
        next(generator)
    assert sesi_palsu.ditutup is True


def test_get_db_tetap_menutup_sesi_walau_request_gagal(sesi_palsu):
    generator = database.get_db()
    next(generator)

    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("request gagal di tengah jalan"))
    assert sesi_palsu.ditutup is True
