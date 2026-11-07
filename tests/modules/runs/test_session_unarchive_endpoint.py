"""GET /measurements?archived=true dan PATCH /measurements/{id}/unarchive. PBI-11 AC10, SCRUM-263.

Daftar arsip memakai kolom yang sama dengan daftar utama. Unarchive mengembalikan sesi
ke daftar utama tanpa mengubah status atau datanya, dan tidak ada DELETE (AC10).
Admin merapikan semua sesi, Developer AI hanya sesi yang ia buat (D10).

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

LIST_URL = "/measurements"
WAKTU_ARSIP = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/unarchive"


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-unarsip@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def developer_ai(db_session) -> User:
    """Akun yang sama dengan pengguna as_role, supaya sesinya tercatat miliknya."""
    user = User(
        id=USER_ID_QA, email="dev-ai-unarsip@veritask.test", name="Dev AI", role=Role.DEVELOPER_AI
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Unarsip")
    db_session.add(item)
    db_session.commit()
    return item


def _arsip(db_session, produk, pembuat, **ubah) -> MeasurementSession:
    isi = {"status": SessionStatus.FINISHED, "archived_at": WAKTU_ARSIP, "archived_by": pembuat.id}
    isi.update(ubah)
    return _sesi(db_session, produk, pembuat, **isi)


def _baca(db_session, session_id: uuid.UUID) -> MeasurementSession:
    db_session.expire_all()
    return db_session.get(MeasurementSession, session_id)


def test_daftar_arsip_hanya_memuat_sesi_arsip(as_role, db_session, produk, pembuat):
    """Positif: sesi arsip tampil di ?archived=true dengan kolom daftar, bukan di daftar default."""
    # Arrange
    aktif = _sesi(db_session, produk, pembuat, label="aktif", status=SessionStatus.FINISHED)
    arsip = _arsip(db_session, produk, pembuat, label="arsip")
    client = as_role(Role.ADMIN)

    # Act
    diarsipkan = client.get(LIST_URL, params={"archived": "true"})
    utama = client.get(LIST_URL)

    # Assert
    assert diarsipkan.status_code == 200
    (item,) = diarsipkan.json()
    assert item["id"] == str(arsip.id)
    assert item["label"] == "arsip"
    assert item["product_name"] == "Produk Unarsip"
    assert item["created_by_name"] == "Admin Sesi"
    assert set(item) == set(utama.json()[0])
    assert [baris["id"] for baris in utama.json()] == [str(aktif.id)]


def test_daftar_arsip_kosong_bila_tidak_ada_sesi_arsip(as_role, db_session, produk, pembuat):
    """Edge: tanpa sesi arsip hasilnya list kosong, bukan error."""
    # Arrange
    _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    response = as_role(Role.ADMIN).get(LIST_URL, params={"archived": "true"})

    # Assert
    assert response.status_code == 200
    assert response.json() == []


def test_archived_false_sama_dengan_daftar_default(as_role, db_session, produk, pembuat):
    """Edge: archived=false tidak menampilkan sesi arsip."""
    # Arrange
    _arsip(db_session, produk, pembuat)

    # Act
    response = as_role(Role.ADMIN).get(LIST_URL, params={"archived": "false"})

    # Assert
    assert response.status_code == 200
    assert response.json() == []


def test_nilai_archived_yang_tidak_valid_ditolak_422(as_role):
    """Negatif: nilai bukan boolean ditolak oleh validasi query."""
    # Act
    response = as_role(Role.ADMIN).get(LIST_URL, params={"archived": "mungkin"})

    # Assert
    assert response.status_code == 422


def test_developer_ai_melihat_semua_sesi_arsip(as_role, db_session, produk, pembuat):
    """Positif: seperti daftar utama, Developer AI melihat sesi arsip buatan siapa pun."""
    # Arrange
    arsip = _arsip(db_session, produk, pembuat)

    # Act
    response = as_role(Role.DEVELOPER_AI).get(LIST_URL, params={"archived": "true"})

    # Assert
    assert response.status_code == 200
    assert [baris["id"] for baris in response.json()] == [str(arsip.id)]


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_tidak_bisa_melihat_daftar_arsip(as_role, role):
    """Negatif: daftar arsip dijaga sama seperti daftar utama."""
    # Act
    response = as_role(role).get(LIST_URL, params={"archived": "true"})

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_admin_mengembalikan_sesi_ke_daftar_utama(as_role, db_session, produk, pembuat):
    """Positif: archived_at dan archived_by kosong, status tetap, sesi tampil di daftar utama."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    client = as_role(Role.ADMIN)

    # Act
    response = client.patch(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"id": str(sesi.id), "status": "finished_with_failures"}
    tersimpan = _baca(db_session, sesi.id)
    assert tersimpan.archived_at is None
    assert tersimpan.archived_by is None
    assert tersimpan.status == SessionStatus.FINISHED_WITH_FAILURES
    assert [baris["id"] for baris in client.get(LIST_URL).json()] == [str(sesi.id)]
    assert client.get(LIST_URL, params={"archived": "true"}).json() == []


def test_developer_ai_mengembalikan_sesi_miliknya(as_role, db_session, produk, developer_ai):
    """Positif: Developer AI memulihkan sesi yang ia buat."""
    # Arrange
    sesi = _arsip(db_session, produk, developer_ai)

    # Act
    response = as_role(Role.DEVELOPER_AI).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    assert _baca(db_session, sesi.id).archived_at is None


def test_developer_ai_tidak_boleh_mengembalikan_sesi_orang_lain(
    as_role, db_session, produk, pembuat
):
    """Negatif: sesi buatan Admin tetap di arsip."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat)

    # Act
    response = as_role(Role.DEVELOPER_AI).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_YOUR_SESSION"
    assert _baca(db_session, sesi.id).archived_by == pembuat.id


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(
    as_role, db_session, produk, pembuat, role
):
    """Negatif: Kontributor dan Viewer tidak bisa memulihkan, sesinya tetap di arsip."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat)

    # Act
    response = as_role(role).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert _baca(db_session, sesi.id).archived_by == pembuat.id


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login tidak ada yang dipulihkan."""
    # Act
    response = client.patch(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_sesi_yang_tidak_ada_ditolak_404(as_role):
    """Negatif: id sesi yang tidak dikenal."""
    # Act
    response = as_role(Role.ADMIN).patch(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_sesi_yang_belum_diarsipkan_ditolak_409(as_role, db_session, produk, pembuat):
    """Negatif: sesi di daftar utama tidak bisa dipulihkan."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    response = as_role(Role.ADMIN).patch(_url(sesi.id))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_NOT_ARCHIVED"


def test_unarchive_kedua_ditolak_409(as_role, db_session, produk, pembuat):
    """Edge: permintaan ganda berurutan, yang kedua ditolak dan tidak mengubah apa pun."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat)
    client = as_role(Role.ADMIN)
    client.patch(_url(sesi.id))

    # Act
    response = client.patch(_url(sesi.id))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_NOT_ARCHIVED"


def test_sesi_bisa_diarsipkan_lagi_setelah_dipulihkan(as_role, db_session, produk, pembuat):
    """Edge: siklus arsip, pulihkan, arsip lagi berjalan dan pelaku arsip terbaru tersimpan."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat)
    client = as_role(Role.ADMIN)
    client.patch(_url(sesi.id))

    # Act
    response = client.patch(f"/measurements/{sesi.id}/archive")

    # Assert
    assert response.status_code == 200
    assert _baca(db_session, sesi.id).archived_by == USER_ID_QA


def test_tidak_ada_endpoint_delete_sesi(as_role, db_session, produk, pembuat):
    """Negatif: sesi tidak pernah dihapus, DELETE tidak tersedia (AC10)."""
    # Arrange
    sesi = _arsip(db_session, produk, pembuat)

    # Act
    response = as_role(Role.ADMIN).delete(f"/measurements/{sesi.id}")

    # Assert
    assert response.status_code == 405
