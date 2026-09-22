"""Unit test app/modules/health/service.py dengan engine pengganti.

PBI-1, sub task [QA] SCRUM-96. test_health.py menerima database "ok"
ATAU "unreachable", tergantung ada PostgreSQL yang menyala di mesin
yang menjalankan test. Di sini kedua jalur dibuat pasti dengan
mengganti get_engine (stub), jadi hasilnya sama di mesin mana pun.
"""

from sqlalchemy import create_engine

from app.modules.health import service


def test_database_ok_kalau_koneksi_berhasil(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(service, "get_engine", lambda: engine)

    hasil = service.check_health()

    assert hasil.status == "ok"
    assert hasil.database == "ok"
    engine.dispose()


class EngineTerputus:
    def connect(self):
        raise ConnectionError("database mati")


def test_database_unreachable_kalau_koneksi_gagal(monkeypatch):
    monkeypatch.setattr(service, "get_engine", lambda: EngineTerputus())

    hasil = service.check_health()

    assert hasil.status == "ok"
    assert hasil.database == "unreachable"
