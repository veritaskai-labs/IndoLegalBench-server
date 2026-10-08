"""Unit test retensi 90 hari dan job hariannya, dependency di-mock.

PBI-18 AC7. Penghapusan sungguhan di PostgreSQL, termasuk penolakan
trigger untuk role lain, dibuktikan di
tests/integration/test_audit_retention_pg.py.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.audit import retention_job, service

SEKARANG = datetime(2026, 10, 8, 2, 0, tzinfo=UTC)


@pytest.fixture
def repo():
    with patch.object(service, "repository") as palsu:
        palsu.delete_older_than.return_value = 3
        yield palsu


def test_batas_waktu_tepat_90_hari_sebelum_sekarang(repo):
    # Arrange
    db = MagicMock()

    # Act
    service.purge_expired(db, now=SEKARANG)

    # Assert
    repo.delete_older_than.assert_called_once_with(db, SEKARANG - timedelta(days=90))


def test_hapus_lalu_commit_dan_kembalikan_jumlah(repo):
    # Arrange
    db = MagicMock()

    # Act
    jumlah = service.purge_expired(db, now=SEKARANG)

    # Assert
    assert jumlah == 3
    db.commit.assert_called_once()


def test_jumlah_terhapus_dicatat_di_log_aplikasi(repo, caplog):
    """AC7: jumlah catatan yang dihapus per eksekusi dicatat di log aplikasi."""
    # Act
    with caplog.at_level(logging.INFO, logger=service.logger.name):
        service.purge_expired(MagicMock(), now=SEKARANG)

    # Assert
    assert "3 audit_logs rows deleted" in caplog.text
    assert "2026-07-10" in caplog.text


def test_tanpa_catatan_kedaluwarsa_tetap_tercatat_nol(repo, caplog):
    # Arrange
    repo.delete_older_than.return_value = 0

    # Act
    with caplog.at_level(logging.INFO, logger=service.logger.name):
        jumlah = service.purge_expired(MagicMock(), now=SEKARANG)

    # Assert
    assert jumlah == 0
    assert "0 audit_logs rows deleted" in caplog.text


def test_gagal_hapus_tidak_commit(repo):
    """Trigger menolak (mis. role salah): tidak ada commit setengah jalan."""
    # Arrange
    db = MagicMock()
    repo.delete_older_than.side_effect = RuntimeError("audit_logs is append-only")

    # Act
    with pytest.raises(RuntimeError):
        service.purge_expired(db, now=SEKARANG)

    # Assert
    db.commit.assert_not_called()


def test_tanpa_waktu_memakai_jam_sekarang(repo):
    # Act
    service.purge_expired(MagicMock())

    # Assert
    batas = repo.delete_older_than.call_args.args[1]
    selisih = datetime.now(UTC) - timedelta(days=90) - batas
    assert timedelta(0) <= selisih < timedelta(seconds=5)


# --- Job harian ---------------------------------------------------------------


def test_job_menolak_jalan_tanpa_url_role_retensi(caplog):
    """Koneksi aplikasi biasa akan ditolak trigger, jadi job tidak mencoba sama sekali."""
    # Arrange
    pengaturan = MagicMock(audit_retention_database_url="")

    # Act
    with (
        patch.object(retention_job, "get_settings", return_value=pengaturan),
        patch.object(retention_job, "create_engine") as engine,
        caplog.at_level(logging.ERROR, logger=retention_job.logger.name),
    ):
        kode = retention_job.main()

    # Assert
    assert kode == 1
    engine.assert_not_called()
    assert "AUDIT_RETENTION_DATABASE_URL" in caplog.text


def test_job_memakai_url_role_retensi_lalu_menghapus():
    # Arrange
    url = "postgresql+psycopg://ilb_retention@db/indolegalbench"
    pengaturan = MagicMock(audit_retention_database_url=url)

    # Act
    with (
        patch.object(retention_job, "get_settings", return_value=pengaturan),
        patch.object(retention_job, "create_engine") as engine,
        patch.object(retention_job, "Session") as session,
        patch.object(retention_job.audit_service, "purge_expired", return_value=5) as purge,
    ):
        kode = retention_job.main()

    # Assert
    assert kode == 0
    engine.assert_called_once_with(url)
    purge.assert_called_once_with(session.return_value.__enter__.return_value)
    engine.return_value.dispose.assert_called_once()
