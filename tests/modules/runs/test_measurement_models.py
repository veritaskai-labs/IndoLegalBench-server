"""SCRUM-205: model ORM tabel pengukuran di SQLite.

Aturan databasenya dibuktikan di PostgreSQL (tests/integration/*_pg.py). Di sini
yang dikunci adalah perilaku model saat dipakai tes SQLite dan kode aplikasi:
enum tersimpan sebagai nilai huruf kecil, index "satu sesi otomatis berjalan"
tetap parsial, dan salinan produk tidak punya kolom kredensial.

Model diambil lewat fixture supaya model yang belum ada gagal per tes, bukan
menghentikan seluruh pengumpulan tes.
"""

import importlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, MeasurementType
from app.shared.security import Role


@pytest.fixture
def runs_models():
    return importlib.import_module("app.modules.runs.models")


@pytest.fixture
def admin(db_session) -> User:
    user = User(email="admin-205@veritask.test", name="Admin", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session, admin) -> AiProduct:
    prod = AiProduct(
        name="Justisio",
        created_by=admin.id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Justisio",
        site_url="https://justisio.test",
    )
    db_session.add(prod)
    db_session.commit()
    return prod


def _sesi(m, produk, admin, **ubah):
    isi = {
        "label": "Uji coba Oktober",
        "purpose": m.SessionPurpose.TRIAL,
        "session_type": m.SessionType.AUTOMATIC,
        "product_id": produk.id,
        "repetitions": 3,
        "split_filter": m.SplitFilter.BOTH,
        "created_by": admin.id,
    }
    isi.update(ubah)
    return m.MeasurementSession(**isi)


def test_sesi_baru_menunggu_dan_enum_tersimpan_huruf_kecil(db_session, runs_models, produk, admin):
    # Arrange
    sesi = _sesi(runs_models, produk, admin)

    # Act
    db_session.add(sesi)
    db_session.commit()
    baris = db_session.execute(
        text("SELECT purpose, session_type, split_filter, status FROM measurement_sessions")
    ).one()

    # Assert
    assert sesi.status == runs_models.SessionStatus.QUEUED
    assert tuple(baris) == ("trial", "automatic", "both", "queued")


def test_sesi_otomatis_kedua_boleh_mengantre(db_session, runs_models, produk, admin):
    """Index parsial: tanpa sqlite_where, SQLite membuatnya UNIQUE penuh pada session_type."""
    # Arrange
    m = runs_models
    berjalan = _sesi(m, produk, admin, status=m.SessionStatus.RUNNING)
    menunggu = _sesi(m, produk, admin, label="Antre")
    manual_a = _sesi(m, produk, admin, session_type=m.SessionType.MANUAL, status="running")
    manual_b = _sesi(m, produk, admin, session_type=m.SessionType.MANUAL, status="running")

    # Act
    db_session.add_all([berjalan, menunggu, manual_a, manual_b])
    db_session.commit()

    # Assert
    assert db_session.query(m.MeasurementSession).count() == 4


def test_sesi_otomatis_berjalan_kedua_ditolak(db_session, runs_models, produk, admin):
    # Arrange
    m = runs_models
    db_session.add(_sesi(m, produk, admin, status=m.SessionStatus.RUNNING))
    db_session.commit()

    # Act / Assert
    db_session.add(_sesi(m, produk, admin, label="Kedua", status=m.SessionStatus.RUNNING))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_salinan_produk_tanpa_kolom_kredensial(runs_models):
    """AC6: data produk disalin tanpa kredensial maupun petunjuknya."""
    kolom = runs_models.SessionProductCopy.__table__.columns.keys()

    assert "name" in kolom
    assert not [nama for nama in kolom if "credential" in nama]


# SCRUM-214, catatan Adra di #89: CHECK label dan alasan "terima apa adanya" dulu memakai
# trim() biasa, jadi tab, NBSP, atau zero-width saja lolos. CHECK model berlaku juga di
# SQLite (create_all), jadi tes unit ikut menjaganya.
KOSONG_TAK_TERLIHAT = {"tab-baris": "\t\n", "nbsp": "\u00a0", "zwsp": "\u200b", "bom": "\ufeff"}


@pytest.mark.parametrize("label", list(KOSONG_TAK_TERLIHAT.values()), ids=list(KOSONG_TAK_TERLIHAT))
def test_label_yang_hanya_berisi_karakter_kosong_ditolak_database(
    db_session, runs_models, produk, admin, label
):
    """Negatif: database menolak label kosong walau API dilewati."""
    # Act / Assert
    db_session.add(_sesi(runs_models, produk, admin, label=label))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_label_dengan_zero_width_di_tengah_diterima_database(
    db_session, runs_models, produk, admin
):
    """Edge: hanya label yang seluruhnya kosong yang ditolak."""
    # Act
    db_session.add(_sesi(runs_models, produk, admin, label="Run\u200bNovember"))
    db_session.commit()

    # Assert
    assert db_session.query(runs_models.MeasurementSession).one().label == "Run\u200bNovember"


@pytest.mark.parametrize(
    "alasan", list(KOSONG_TAK_TERLIHAT.values()), ids=list(KOSONG_TAK_TERLIHAT)
)
def test_alasan_terima_yang_hanya_berisi_karakter_kosong_ditolak_database(
    db_session, runs_models, produk, admin, alasan
):
    """Negatif: alasan 'terima apa adanya' wajib berisi teks nyata (AC9)."""
    # Arrange
    m = runs_models
    sesi = _sesi(m, produk, admin, status=m.SessionStatus.FINISHED_WITH_FAILURES)
    db_session.add(sesi)
    db_session.commit()

    # Act / Assert
    sesi.accepted_as_is_at = datetime(2026, 11, 4, tzinfo=UTC)
    sesi.accepted_as_is_by = admin.id
    sesi.accepted_as_is_reason = alasan
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
