"""PATCH dan DELETE /measurements/{id}/accept: terima apa adanya. PBI-12 AC9, SCRUM-214.

"Terima apa adanya" adalah flag, bukan status: sesi finished_with_failures dianggap
selesai sepenuhnya bila Admin menandainya dengan alasan tertulis, dan pelaku serta
waktunya tersimpan. Hanya Admin. Alasan kosong ditolak 422 REASON_REQUIRED.

Keputusan Rafa 4 Nov (catatan Adra di #89 bahwa database membolehkan set dan hapus):
- Admin boleh menghapus tanda itu (DELETE), lalu menandai ulang.
- Selama tertanda, sesi tidak bisa dicoba ulang (diuji di test_session_status_repository).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

DITERIMA = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/accept"


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-terima@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Terima")
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def gagal_sebagian(db_session, produk, pembuat) -> MeasurementSession:
    return _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)


def _baca(db_session, session_id: uuid.UUID) -> MeasurementSession:
    db_session.expire_all()
    return db_session.get(MeasurementSession, session_id)


def _tandai(db_session, sesi: MeasurementSession, alasan: str = "Alasan lama") -> None:
    sesi.accepted_as_is_at = DITERIMA
    sesi.accepted_as_is_by = sesi.created_by
    sesi.accepted_as_is_reason = alasan
    db_session.commit()


def test_admin_menerima_sesi_dengan_kegagalan(as_role, db_session, gagal_sebagian):
    """Positif: alasan, pelaku, dan waktu tersimpan; status tetap finished_with_failures."""
    # Act
    response = as_role(Role.ADMIN).patch(
        _url(gagal_sebagian.id), json={"reason": "Kegagalan dari penyedia, bukan model"}
    )

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(gagal_sebagian.id)
    assert body["accepted_as_is_reason"] == "Kegagalan dari penyedia, bukan model"
    assert body["accepted_as_is_by"] == str(USER_ID_QA)
    assert body["accepted_as_is_at"] is not None
    tersimpan = _baca(db_session, gagal_sebagian.id)
    assert tersimpan.status == SessionStatus.FINISHED_WITH_FAILURES
    assert tersimpan.accepted_as_is_by == USER_ID_QA
    assert tersimpan.accepted_as_is_at is not None


def test_alasan_disimpan_tanpa_spasi_di_tepi(as_role, db_session, gagal_sebagian):
    """Edge: spasi di awal dan akhir alasan dibuang sebelum disimpan."""
    # Act
    response = as_role(Role.ADMIN).patch(_url(gagal_sebagian.id), json={"reason": "  Cukup  "})

    # Assert
    assert response.status_code == 200
    assert _baca(db_session, gagal_sebagian.id).accepted_as_is_reason == "Cukup"


@pytest.mark.parametrize("isi", [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}])
def test_tanpa_alasan_ditolak(as_role, db_session, gagal_sebagian, isi):
    """Negatif: alasan wajib (AC9); kosong, null, atau hanya spasi = 422 REASON_REQUIRED."""
    # Act
    response = as_role(Role.ADMIN).patch(_url(gagal_sebagian.id), json=isi)

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "REASON_REQUIRED"
    assert _baca(db_session, gagal_sebagian.id).accepted_as_is_at is None


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.QUEUED,
        SessionStatus.RUNNING,
        SessionStatus.PAUSED,
        SessionStatus.CANCELLED,
        SessionStatus.FINISHED,
        SessionStatus.FAILED,
    ],
)
def test_hanya_sesi_selesai_dengan_kegagalan_yang_bisa_diterima(
    as_role, db_session, produk, pembuat, status
):
    """Negatif: status lain ditolak 409 SESSION_NOT_ACCEPTABLE, tanda tidak tersimpan."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=status)

    # Act
    response = as_role(Role.ADMIN).patch(_url(sesi.id), json={"reason": "Alasan"})

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_NOT_ACCEPTABLE"
    assert _baca(db_session, sesi.id).accepted_as_is_at is None


def test_sesi_yang_sudah_diterima_tidak_ditimpa(as_role, db_session, gagal_sebagian):
    """Negatif: menerima dua kali = 409 ALREADY_ACCEPTED; alasan pertama tetap (hapus dulu)."""
    # Arrange
    _tandai(db_session, gagal_sebagian)

    # Act
    response = as_role(Role.ADMIN).patch(_url(gagal_sebagian.id), json={"reason": "Alasan baru"})

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_ACCEPTED"
    assert _baca(db_session, gagal_sebagian.id).accepted_as_is_reason == "Alasan lama"


@pytest.mark.parametrize("peran", [Role.DEVELOPER_AI, Role.VIEWER, Role.CONTRIBUTOR])
@pytest.mark.parametrize("metode", ["patch", "delete"])
def test_hanya_admin(as_role, db_session, gagal_sebagian, peran, metode):
    """Negatif: Developer AI, Viewer, dan Kontributor tidak boleh menerima atau menghapus (A01)."""
    # Arrange
    kirim = getattr(as_role(peran), metode)
    kwargs = {"json": {"reason": "Alasan"}} if metode == "patch" else {}

    # Act
    response = kirim(_url(gagal_sebagian.id), **kwargs)

    # Assert
    assert response.status_code == 403
    assert _baca(db_session, gagal_sebagian.id).accepted_as_is_at is None


@pytest.mark.parametrize("metode", ["patch", "delete"])
def test_sesi_tidak_ada(as_role, metode):
    """Negatif: id asing = 404 MEASUREMENT_NOT_FOUND."""
    # Arrange
    kirim = getattr(as_role(Role.ADMIN), metode)
    kwargs = {"json": {"reason": "Alasan"}} if metode == "patch" else {}

    # Act
    response = kirim(_url(uuid.uuid4()), **kwargs)

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_admin_menghapus_tanda_terima(as_role, db_session, gagal_sebagian):
    """Positif: DELETE mengosongkan waktu, pelaku, dan alasan; status tetap."""
    # Arrange
    _tandai(db_session, gagal_sebagian)

    # Act
    response = as_role(Role.ADMIN).delete(_url(gagal_sebagian.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["accepted_as_is_at"] is None
    assert body["accepted_as_is_by"] is None
    assert body["accepted_as_is_reason"] is None
    tersimpan = _baca(db_session, gagal_sebagian.id)
    assert tersimpan.accepted_as_is_at is None
    assert tersimpan.status == SessionStatus.FINISHED_WITH_FAILURES


def test_menghapus_yang_belum_diterima_ditolak(as_role, gagal_sebagian):
    """Negatif: tidak ada tanda untuk dihapus = 409 NOT_ACCEPTED."""
    # Act
    response = as_role(Role.ADMIN).delete(_url(gagal_sebagian.id))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "NOT_ACCEPTED"


def test_setelah_dihapus_bisa_diterima_lagi(as_role, db_session, gagal_sebagian):
    """Edge: hapus lalu terima ulang dengan alasan baru."""
    # Arrange
    _tandai(db_session, gagal_sebagian)
    client = as_role(Role.ADMIN)
    client.delete(_url(gagal_sebagian.id))

    # Act
    response = client.patch(_url(gagal_sebagian.id), json={"reason": "Alasan baru"})

    # Assert
    assert response.status_code == 200
    assert _baca(db_session, gagal_sebagian.id).accepted_as_is_reason == "Alasan baru"
