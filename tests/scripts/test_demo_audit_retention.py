"""Test script demo retensi audit untuk UAT (F-07, PBI-18 AC7).

Database: SQLite in-memory dari fixture db_session. SQLite memblokir
semua DELETE di audit_logs lewat trigger, jadi job palsu di sini
membuang trigger itu dulu untuk meniru role ilb_retention. Penolakan
trigger yang sungguhan dibuktikan di
tests/integration/test_demo_audit_retention_pg.py.

Isolasi: monkeypatch mengganti get_settings, pabrik session, dan
retention_job.main, supaya test tidak pernah menyentuh database atau
job yang asli.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.modules.audit import repository, service
from app.modules.audit.models import AuditLog
from scripts import demo_audit_retention as demo

SEKARANG = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
URL_DEV = (
    "postgresql+psycopg://indolegalbench:rahasia@db:5432/indolegalbench"  # pragma: allowlist secret
)
URL_RETENSI = (
    "postgresql+psycopg://ilb_retention:rahasia@db:5432/indolegalbench"  # pragma: allowlist secret
)


def _alasan(**ubah) -> str | None:
    nilai = {
        "confirmed": True,
        "app_env": "staging",
        "database_url": URL_DEV,
        "retention_url": URL_RETENSI,
    }
    nilai.update(ubah)
    return demo.refusal_reason(**nilai)


# --- Penjaga sebelum menyentuh database ----------------------------------------


@pytest.mark.parametrize("app_env", ["local", "staging"])
def test_stack_dev_atau_lokal_dengan_flag_diizinkan(app_env):
    # Act + Assert
    assert _alasan(app_env=app_env) is None


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_database_di_mesin_sendiri_diizinkan(host):
    # Arrange
    url = f"postgresql+psycopg://indolegalbench:rahasia@{host}:5432/indolegalbench"  # pragma: allowlist secret

    # Act + Assert
    assert _alasan(database_url=url, retention_url=url) is None


def test_tanpa_flag_konfirmasi_ditolak():
    # Act
    alasan = _alasan(confirmed=False)

    # Assert
    assert demo.CONFIRM_FLAG in alasan


def test_production_ditolak_walau_ada_flag():
    # Act
    alasan = _alasan(app_env="production")

    # Assert
    assert "production" in alasan


@pytest.mark.parametrize("app_env", ["", "prod", "Staging"])
def test_app_env_yang_tidak_dikenal_ditolak(app_env):
    # Act + Assert
    assert _alasan(app_env=app_env) is not None


@pytest.mark.parametrize("kolom", ["database_url", "retention_url"])
def test_database_di_host_lain_ditolak(kolom):
    # Arrange
    url = "postgresql+psycopg://indolegalbench:rahasia@api.indolegalbench.veritask.ai/x"  # pragma: allowlist secret

    # Act
    alasan = _alasan(**{kolom: url})

    # Assert
    assert "api.indolegalbench.veritask.ai" in alasan
    assert "rahasia" not in alasan


def test_url_role_retensi_kosong_ditolak():
    # Act
    alasan = _alasan(retention_url="")

    # Assert
    assert "AUDIT_RETENTION_DATABASE_URL" in alasan


@pytest.mark.parametrize("url", ["sqlite://", "bukan-url"])
def test_url_tanpa_host_atau_rusak_ditolak(url):
    # Act + Assert
    assert _alasan(database_url=url) is not None


# --- Batas 90 hari ---------------------------------------------------------------


def test_lewat_90_hari_kedaluwarsa():
    # Act + Assert
    assert demo.is_expired(SEKARANG - timedelta(days=91), SEKARANG)


def test_kurang_dari_90_hari_tidak_kedaluwarsa():
    # Act + Assert
    assert not demo.is_expired(SEKARANG - timedelta(days=89), SEKARANG)


def test_tepat_90_hari_belum_kedaluwarsa():
    """Sama dengan trigger dan repository: hanya yang lebih tua dari batas (<)."""
    # Act + Assert
    assert not demo.is_expired(SEKARANG - timedelta(days=service.RETENTION_DAYS), SEKARANG)
    assert demo.is_expired(
        SEKARANG - timedelta(days=service.RETENTION_DAYS, microseconds=1), SEKARANG
    )


def test_umur_data_uji_mengapit_batas_90_hari():
    # Act + Assert
    assert demo.DEMO_AGES_DAYS == (service.RETENTION_DAYS + 1, service.RETENTION_DAYS - 1)


# --- Data uji ---------------------------------------------------------------------


def test_data_uji_dibuat_dengan_tanggal_lampau_dan_tanda_sintetis(db_session):
    # Act
    baris = demo.seed_demo_rows(db_session, SEKARANG)

    # Assert
    assert [b.age_days for b in baris] == [91, 89]
    tersimpan = {row.id: row for row in db_session.query(AuditLog)}
    for b in baris:
        row = tersimpan[b.id]
        assert row.occurred_at.replace(tzinfo=UTC) == SEKARANG - timedelta(days=b.age_days)
        assert row.reason == demo.DEMO_REASON
        assert row.actor_user_id is None
    assert len(tersimpan) == 2


def test_existing_ids_hanya_mengembalikan_yang_masih_ada(db_session):
    # Arrange
    baris = demo.seed_demo_rows(db_session, SEKARANG)

    # Act
    ada = demo.existing_ids(db_session, [b.id for b in baris] + [999_999])

    # Assert
    assert ada == {b.id for b in baris}


def test_existing_ids_daftar_kosong(db_session):
    # Act + Assert
    assert demo.existing_ids(db_session, []) == set()


# --- Laporan untuk klien -----------------------------------------------------------


def _baris_contoh() -> list[demo.DemoRow]:
    return [
        demo.DemoRow(id=11, age_days=91, occurred_at=SEKARANG - timedelta(days=91)),
        demo.DemoRow(id=12, age_days=89, occurred_at=SEKARANG - timedelta(days=89)),
    ]


def test_hasil_sesuai_kalau_yang_tua_hilang_dan_yang_muda_tetap():
    # Act + Assert
    assert demo.outcome_ok(_baris_contoh(), after={12}, now=SEKARANG)


@pytest.mark.parametrize("sesudah", [{11, 12}, set(), {11}])
def test_hasil_tidak_sesuai_untuk_kombinasi_lain(sesudah):
    # Act + Assert
    assert not demo.outcome_ok(_baris_contoh(), after=sesudah, now=SEKARANG)


def test_laporan_sesuai_mudah_dibaca():
    # Act
    laporan = demo.render_report(_baris_contoh(), before={11, 12}, after={12}, now=SEKARANG)

    # Assert
    assert "19 Okt 2026 10:00 UTC" in laporan
    assert "21 Jul 2026 10:00 UTC" in laporan  # batas 90 hari
    assert "#11" in laporan and "20 Jul 2026" in laporan and "91 hari" in laporan
    assert "#12" in laporan and "22 Jul 2026" in laporan and "89 hari" in laporan
    baris_tua = next(b for b in laporan.splitlines() if "#11" in b)
    baris_muda = next(b for b in laporan.splitlines() if "#12" in b)
    assert "Terhapus" in baris_tua and "Ada" in baris_tua
    assert "Terhapus" not in baris_muda
    assert "HASIL: SESUAI" in laporan


def test_laporan_tidak_sesuai_ditandai_jelas():
    # Act
    laporan = demo.render_report(_baris_contoh(), before={11, 12}, after={11, 12}, now=SEKARANG)

    # Assert
    assert "HASIL: TIDAK SESUAI" in laporan
    assert "TIDAK" in next(b for b in laporan.splitlines() if "#11" in b)


def test_laporan_menandai_baris_yang_sudah_hilang_sebelum_job():
    # Act
    laporan = demo.render_report(_baris_contoh(), before={12}, after={12}, now=SEKARANG)

    # Assert
    baris_tua = next(b for b in laporan.splitlines() if "#11" in b)
    assert baris_tua.count("Terhapus") == 3  # sebelum, sesudah, seharusnya


# --- main --------------------------------------------------------------------------


@pytest.fixture
def lingkungan(monkeypatch, db_session):
    """Setelan dev palsu dan pabrik session yang memakai SQLite in-memory."""
    pengaturan = SimpleNamespace(
        app_env="staging", database_url=URL_DEV, audit_retention_database_url=URL_RETENSI
    )
    monkeypatch.setattr(demo, "get_settings", lambda: pengaturan)
    monkeypatch.setattr(demo, "get_session_factory", lambda: lambda: db_session)
    return SimpleNamespace(pengaturan=pengaturan, db=db_session)


def _job_palsu(db, *, kode=0, panggilan=None):
    """Meniru role ilb_retention di SQLite: buang trigger, lalu hapus yang kedaluwarsa."""

    def main() -> int:
        if panggilan is not None:
            panggilan.append(True)
        db.execute(text("DROP TRIGGER IF EXISTS audit_logs_block_delete"))
        repository.delete_older_than(db, datetime.now(UTC) - timedelta(days=service.RETENTION_DAYS))
        db.commit()
        return kode

    return main


def test_main_menjalankan_job_lalu_mencetak_hasil_sesuai(lingkungan, monkeypatch, capsys):
    # Arrange
    panggilan: list[bool] = []
    monkeypatch.setattr(demo.retention_job, "main", _job_palsu(lingkungan.db, panggilan=panggilan))

    # Act
    kode = demo.main([demo.CONFIRM_FLAG])

    # Assert
    keluaran = capsys.readouterr().out
    assert kode == 0
    assert panggilan == [True]
    assert "HASIL: SESUAI" in keluaran
    assert "rahasia" not in keluaran
    assert lingkungan.db.query(AuditLog).count() == 1


def test_main_tanpa_flag_menolak_sebelum_menyentuh_database(lingkungan, monkeypatch, capsys):
    # Arrange
    monkeypatch.setattr(demo, "get_session_factory", pytest.fail)
    monkeypatch.setattr(demo.retention_job, "main", pytest.fail)

    # Act
    kode = demo.main([])

    # Assert
    assert kode == 2
    assert "DITOLAK" in capsys.readouterr().out


def test_main_di_production_menolak(lingkungan, monkeypatch, capsys):
    # Arrange
    lingkungan.pengaturan.app_env = "production"
    monkeypatch.setattr(demo, "get_session_factory", pytest.fail)

    # Act
    kode = demo.main([demo.CONFIRM_FLAG])

    # Assert
    assert kode == 2
    assert "production" in capsys.readouterr().out


def test_main_gagal_kalau_job_gagal(lingkungan, monkeypatch, capsys):
    # Arrange
    monkeypatch.setattr(demo.retention_job, "main", lambda: 1)

    # Act
    kode = demo.main([demo.CONFIRM_FLAG])

    # Assert
    keluaran = capsys.readouterr().out
    assert kode == 1
    assert "job retensi gagal" in keluaran
    assert "HASIL" not in keluaran


def test_main_gagal_kalau_job_tidak_menghapus_apa_pun(lingkungan, monkeypatch, capsys):
    """Job jalan tapi trigger menolak: laporan harus jujur bilang tidak sesuai."""
    # Arrange
    monkeypatch.setattr(demo.retention_job, "main", lambda: 0)

    # Act
    kode = demo.main([demo.CONFIRM_FLAG])

    # Assert
    assert kode == 1
    assert "HASIL: TIDAK SESUAI" in capsys.readouterr().out


def test_main_boleh_diulang(lingkungan, monkeypatch, capsys):
    """Tiap putaran membuat dua catatan baru. Catatan 89 hari dari putaran lalu tetap ada."""
    # Arrange
    monkeypatch.setattr(demo.retention_job, "main", _job_palsu(lingkungan.db))
    demo.main([demo.CONFIRM_FLAG])
    capsys.readouterr()

    # Act
    kode = demo.main([demo.CONFIRM_FLAG])

    # Assert
    assert kode == 0
    assert "HASIL: SESUAI" in capsys.readouterr().out
    assert lingkungan.db.query(AuditLog).count() == 2
