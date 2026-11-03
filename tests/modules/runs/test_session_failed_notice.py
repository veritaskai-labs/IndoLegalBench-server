"""Notifikasi ke pemulai saat sesi gagal sebelum mulai. PBI-11 AC10, SCRUM-210.

SessionQueue (SCRUM-207) menandai sesi failed bila produknya sudah dihapus atau
nonaktif, kasus layaknya habis, atau terjadi galat tak terduga saat giliran.
Pemulai sesi mendapat satu notifikasi "Pengukuran gagal" di aplikasi, lewat
notify_session_ended yang sama dengan akhir sesi lainnya.

Notifikasi hanya dikirim bila fail() benar-benar mengubah status, supaya sesi
yang sudah ditangani worker lain tidak mendapat notifikasi ganda.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: notifikasi yang tidak boleh terkirim.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.notifications.types import MEASUREMENT_FAILED
from app.modules.runs import dependencies, service
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi
from tests.modules.runs.test_session_queue import (
    ADMIN_ID,
    JAM,
    PRODUCT_ID,
    SESI_BARU,
    SESI_LAMA,
    _menunggu,
    _Penyimpanan,
    _Produk,
    _Snapshot,
)

PRODUK_TERHAPUS = uuid.UUID("00000000-0000-0000-0000-0000000000f8")


class _Pemberitahu:
    """Notifier palsu yang mencatat setiap panggilan notify."""

    def __init__(self) -> None:
        self.terkirim: list[tuple] = []

    def notify(self, user_ids, type, payload) -> None:
        self.terkirim.append((list(user_ids), type, dict(payload)))


class _PenyimpananSudahDitangani(_Penyimpanan):
    """fail() tidak mengubah apa pun: sesi sudah bukan queued, misalnya ditangani worker lain."""

    def fail(self, session_id, *, finished_at) -> bool:
        super().fail(session_id, finished_at=finished_at)
        return False


def _antrean(penyimpanan, *, produk=None, snapshot=None):
    pemberitahu = _Pemberitahu()
    antrean = service.SessionQueue(
        sessions=penyimpanan,
        snapshots=snapshot or _Snapshot(),
        products=produk or _Produk(),
        clock=lambda: JAM,
        notifier=pemberitahu,
    )
    return antrean, pemberitahu


def test_produk_terhapus_pemulai_mendapat_notifikasi_gagal():
    """Positif: satu notifikasi ke pemulai, dengan label sesi dan tautan ke detailnya."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(product_id=PRODUK_TERHAPUS))
    antrean, pemberitahu = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    antrean.take_next()

    # Assert
    assert pemberitahu.terkirim == [
        (
            [ADMIN_ID],
            MEASUREMENT_FAILED,
            {
                "title": "Pengukuran gagal",
                "body": 'Sesi "Run Oktober" gagal.',
                "link": f"/runs/{SESI_LAMA}",
            },
        )
    ]


def test_galat_tak_terduga_saat_mulai_juga_memberi_notifikasi():
    """Positif: sesi yang gagal karena galat database tetap dikabarkan ke pemulainya."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu(), start_galat={SESI_LAMA})
    antrean, pemberitahu = _antrean(penyimpanan)

    # Act
    antrean.take_next()

    # Assert
    assert [kirim[1] for kirim in pemberitahu.terkirim] == [MEASUREMENT_FAILED]


def test_kasus_layak_habis_memberi_notifikasi():
    """Positif: tidak ada lagi kasus untuk filter sesi, sesi gagal dan pemulai dikabari."""
    # Arrange
    penyimpanan = _Penyimpanan(menunggu=_menunggu())
    antrean, pemberitahu = _antrean(penyimpanan, snapshot=_Snapshot(items=[]))

    # Act
    antrean.take_next()

    # Assert
    assert len(pemberitahu.terkirim) == 1


def test_hanya_sesi_yang_gagal_yang_diberi_notifikasi():
    """Negatif: sesi sehat berikutnya mulai berjalan tanpa notifikasi gagal."""
    # Arrange
    rusak = _menunggu(product_id=PRODUK_TERHAPUS)
    sehat = _menunggu(sesi_id=SESI_BARU, product_id=PRODUCT_ID)
    penyimpanan = _Penyimpanan(menunggu=[rusak, sehat])
    antrean, pemberitahu = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    hasil = antrean.take_next()

    # Assert
    assert hasil == SESI_BARU
    assert [kirim[2]["link"] for kirim in pemberitahu.terkirim] == [f"/runs/{SESI_LAMA}"]


def test_sesi_yang_berjalan_normal_tidak_diberi_notifikasi():
    """Negatif: mulai berjalan bukan akhir sesi."""
    # Arrange
    antrean, pemberitahu = _antrean(_Penyimpanan(menunggu=_menunggu()))

    # Act
    antrean.take_next()

    # Assert
    assert pemberitahu.terkirim == []


def test_sesi_yang_sudah_ditangani_worker_lain_tidak_diberi_notifikasi_ganda():
    """Edge: fail() tidak mengubah status, jadi tidak ada notifikasi."""
    # Arrange
    penyimpanan = _PenyimpananSudahDitangani(menunggu=_menunggu(product_id=PRODUK_TERHAPUS))
    antrean, pemberitahu = _antrean(penyimpanan, produk=_Produk(terhapus={PRODUK_TERHAPUS}))

    # Act
    antrean.take_next()

    # Assert
    assert pemberitahu.terkirim == []


@pytest.fixture
def pemulai(db_session) -> User:
    user = User(email="pemulai-gagal@veritask.test", name="Admin Pemulai", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


def test_antrean_aplikasi_menulis_notifikasi_ke_tabel(db_session, pemulai, monkeypatch):
    """Positif: antrean yang dipakai worker menyimpan notifikasi ke database yang sama."""
    # Arrange: produk dihapus setelah sesi masuk antrean
    # Jalur bawaan DbNotifier diarahkan ke tempat lain, supaya terbukti antrean
    # memakai database miliknya sendiri, bukan pengaturan global.
    lain = sessionmaker()
    monkeypatch.setattr("app.modules.notifications.service.get_session_factory", lambda: lain)
    produk = _otomatis("Produk Dihapus")
    produk.deleted_at = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    db_session.add(produk)
    db_session.commit()
    sesi = _sesi(db_session, produk, pemulai, label="Run Gagal", queued_at=JAM)

    # Act
    dependencies.build_session_queue(db_session).take_next()

    # Assert
    db_session.expire_all()
    assert db_session.get(MeasurementSession, sesi.id).status == SessionStatus.FAILED
    (catatan,) = db_session.scalars(select(Notification)).all()
    assert catatan.user_id == pemulai.id
    assert catatan.type == MEASUREMENT_FAILED
    assert catatan.body == 'Sesi "Run Gagal" gagal.'
    assert catatan.link == f"/runs/{sesi.id}"
