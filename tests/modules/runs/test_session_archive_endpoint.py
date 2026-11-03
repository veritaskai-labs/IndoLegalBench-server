"""PATCH /measurements/{id}/archive: arsipkan sesi dari daftar. PBI-11 AC10, SCRUM-210.

Sesi hanya boleh diarsipkan sekali, setelah berakhir (session_rules.ensure_archivable).
Arsip menyembunyikan sesi dari daftar tanpa mengubah status atau menghapus datanya.
Admin boleh mengarsipkan sesi siapa pun. Developer AI hanya sesi yang ia buat
(D10 baris kontrol sesi), walaupun ia melihat semua sesi di daftar.

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


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/archive"


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-arsip@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Arsip")
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def developer_ai(db_session) -> User:
    """Akun yang sama dengan pengguna as_role, supaya sesinya tercatat miliknya."""
    user = User(
        id=USER_ID_QA, email="dev-ai-arsip@veritask.test", name="Dev AI", role=Role.DEVELOPER_AI
    )
    db_session.add(user)
    db_session.commit()
    return user


def _baca(db_session, session_id: uuid.UUID) -> MeasurementSession:
    db_session.expire_all()
    return db_session.get(MeasurementSession, session_id)


def test_admin_mengarsipkan_sesi_selesai(as_role, db_session, produk, pembuat):
    """Positif: pelaku dan waktunya tersimpan, sesinya hilang dari daftar."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    client = as_role(Role.ADMIN)

    # Act
    response = client.patch(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(sesi.id)
    assert body["archived_by"] == str(USER_ID_QA)
    assert body["archived_at"] is not None
    tersimpan = _baca(db_session, sesi.id)
    assert tersimpan.archived_by == USER_ID_QA
    assert tersimpan.archived_at is not None
    assert client.get("/measurements").json() == []


def test_developer_ai_mengarsipkan_sesi_miliknya(as_role, db_session, produk, developer_ai):
    """Positif: Developer AI merapikan sesi yang ia buat sendiri."""
    # Arrange
    sesi = _sesi(db_session, produk, developer_ai, status=SessionStatus.FINISHED_WITH_FAILURES)

    # Act
    response = as_role(Role.DEVELOPER_AI).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    assert _baca(db_session, sesi.id).archived_by == USER_ID_QA


def test_developer_ai_tidak_boleh_mengarsipkan_sesi_orang_lain(
    as_role, db_session, produk, pembuat
):
    """Negatif: sesi buatan Admin tetap tampil walau Developer AI menekan Arsipkan."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    response = as_role(Role.DEVELOPER_AI).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_YOUR_SESSION"
    assert _baca(db_session, sesi.id).archived_at is None


def test_admin_mengarsipkan_sesi_buatan_developer_ai(as_role, db_session, produk):
    """Positif: batas milik sendiri hanya untuk Developer AI, Admin merapikan semua sesi."""
    # Arrange
    lain = User(email="dev-ai-lain@veritask.test", name="Dev AI Lain", role=Role.DEVELOPER_AI)
    db_session.add(lain)
    db_session.commit()
    sesi = _sesi(db_session, produk, lain, status=SessionStatus.FINISHED)

    # Act
    response = as_role(Role.ADMIN).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 200


def test_arsip_tidak_mengubah_status_sesi(as_role, db_session, produk, pembuat):
    """Edge: sesi gagal tetap gagal setelah diarsipkan, hanya disembunyikan."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FAILED)

    # Act
    as_role(Role.ADMIN).patch(_url(sesi.id))

    # Assert
    assert _baca(db_session, sesi.id).status == SessionStatus.FAILED


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(
    as_role, db_session, produk, pembuat, role
):
    """Negatif: Kontributor dan Viewer tidak bisa mengarsip, sesinya tetap tampil."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    response = as_role(role).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert _baca(db_session, sesi.id).archived_at is None


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login tidak ada yang diarsipkan."""
    # Act
    response = client.patch(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("status", [SessionStatus.QUEUED, SessionStatus.RUNNING])
def test_sesi_yang_belum_berakhir_ditolak_409(as_role, db_session, produk, pembuat, status):
    """Negatif: sesi antre atau berjalan tidak boleh hilang dari daftar."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=status)

    # Act
    response = as_role(Role.ADMIN).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_NOT_ARCHIVABLE"
    assert _baca(db_session, sesi.id).archived_at is None


def test_arsip_kedua_ditolak_dan_pelaku_pertama_tetap(as_role, db_session, produk, pembuat):
    """Negatif: arsip hanya sekali, archived_by tetap menunjuk pengarsip pertama."""
    # Arrange
    waktu_pertama = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    sesi = _sesi(
        db_session,
        produk,
        pembuat,
        status=SessionStatus.FINISHED,
        archived_at=waktu_pertama,
        archived_by=pembuat.id,
    )

    # Act
    response = as_role(Role.ADMIN).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_ALREADY_ARCHIVED"
    assert _baca(db_session, sesi.id).archived_by == pembuat.id


def test_sesi_yang_tidak_ada_ditolak_404(as_role):
    """Negatif: id sesi yang tidak dikenal."""
    # Act
    response = as_role(Role.ADMIN).patch(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"
