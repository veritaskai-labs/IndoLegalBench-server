"""GET /measurements: daftar sesi pengukuran di halaman Eksekusi. PBI-11 AC10, SCRUM-210.

Kolom: label, waktu dibuat, pembuat, produk, jumlah kasus, pengulangan, status, dan
progres "X dari N". Sesi yang diarsipkan tidak tampil. Admin dan Developer AI boleh
melihat, sama seperti ringkasan dan progres pengukuran.

Snapshot dan attempts dibuat saat sesi mulai (SCRUM-207), jadi sesi yang masih antre
belum punya jumlah kasus dan progresnya 0 dari 0.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.modules.auth.models import User
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis

URL = "/measurements"
AWAL = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-210@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Uji")
    db_session.add(item)
    db_session.commit()
    return item


def _sesi(db_session, produk, pembuat, **ubah) -> MeasurementSession:
    """Satu sesi sintetis. Bawaannya masih antre, tanpa snapshot dan attempts."""
    isi = {
        "label": "Sesi uji",
        "purpose": SessionPurpose.TRIAL,
        "session_type": SessionType.AUTOMATIC,
        "product_id": produk.id,
        "repetitions": 2,
        "split_filter": SplitFilter.BOTH,
        "created_by": pembuat.id,
        "created_at": AWAL,
    }
    isi.update(ubah)
    sesi = MeasurementSession(**isi)
    db_session.add(sesi)
    db_session.commit()
    return sesi


def _mulai(db_session, sesi, status_percobaan: list[AttemptStatus]) -> None:
    """Snapshot dan attempts seperti saat sesi mulai: satu kasus per dua status (2 pengulangan)."""
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()
    for awal in range(0, len(status_percobaan), sesi.repetitions):
        item = CaseSnapshotItem(
            snapshot_id=snapshot.id,
            case_id=uuid.uuid4(),
            case_version_id=uuid.uuid4(),
            body={"split_tag": "dev"},
        )
        db_session.add(item)
        db_session.flush()
        for nomor, status in enumerate(status_percobaan[awal : awal + sesi.repetitions], 1):
            db_session.add(
                Attempt(
                    session_id=sesi.id,
                    snapshot_item_id=item.id,
                    repetition_no=nomor,
                    source=AttemptSource.AUTOMATIC,
                    status=status,
                )
            )
    db_session.commit()


def test_admin_melihat_kolom_daftar_sesi(as_role, db_session, produk, pembuat):
    """Positif: semua kolom AC10 terisi, progres menghitung gagal teknis sebagai selesai."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, label="Run November", status=SessionStatus.RUNNING)
    _mulai(
        db_session,
        sesi,
        [
            AttemptStatus.SUCCEEDED,
            AttemptStatus.TECHNICAL_FAILURE,
            AttemptStatus.RUNNING,
            AttemptStatus.PENDING,
            AttemptStatus.PENDING,
            AttemptStatus.PENDING,
        ],
    )

    # Act
    response = as_role(Role.ADMIN).get(URL)

    # Assert
    assert response.status_code == 200
    (baris,) = response.json()
    assert baris["id"] == str(sesi.id)
    assert baris["label"] == "Run November"
    assert datetime.fromisoformat(baris["created_at"]).replace(tzinfo=UTC) == AWAL
    assert baris["created_by"] == str(pembuat.id)
    assert baris["created_by_name"] == "Admin Sesi"
    assert baris["product_id"] == str(produk.id)
    assert baris["product_name"] == "Produk Uji"
    assert baris["case_count"] == 3
    assert baris["repetitions"] == 2
    assert baris["status"] == "running"
    assert baris["progress"] == {"completed": 2, "total": 6}


def test_developer_ai_boleh_melihat_daftar_sesi(as_role, db_session, produk, pembuat):
    """Positif: Developer AI ikut memantau pengukuran, sama seperti progres (SCRUM-215)."""
    # Arrange
    _sesi(db_session, produk, pembuat)

    # Act
    response = as_role(Role.DEVELOPER_AI).get(URL)

    # Assert
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(as_role, role):
    """Negatif: Kontributor dan Viewer tidak membuka halaman Eksekusi."""
    # Act
    response = as_role(role).get(URL)

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login tidak ada data sesi yang keluar."""
    # Act
    response = client.get(URL)

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_sesi_yang_diarsipkan_tidak_tampil(as_role, db_session, produk, pembuat):
    """Edge: arsip menyembunyikan sesi dari daftar, datanya tetap ada (AC10)."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="Masih tampil", status=SessionStatus.FINISHED)
    _sesi(
        db_session,
        produk,
        pembuat,
        label="Sudah diarsipkan",
        status=SessionStatus.FINISHED,
        archived_at=AWAL,
        archived_by=pembuat.id,
    )

    # Act
    baris = as_role(Role.ADMIN).get(URL).json()

    # Assert
    assert [item["label"] for item in baris] == ["Masih tampil"]


def test_sesi_antre_belum_punya_jumlah_kasus(as_role, db_session, produk, pembuat):
    """Edge: snapshot dibuat saat mulai, jadi sesi antre bernilai null dan progres 0 dari 0."""
    # Arrange
    _sesi(db_session, produk, pembuat)

    # Act
    (baris,) = as_role(Role.ADMIN).get(URL).json()

    # Assert
    assert baris["status"] == "queued"
    assert baris["case_count"] is None
    assert baris["progress"] == {"completed": 0, "total": 0}


def test_sesi_terbaru_tampil_paling_atas(as_role, db_session, produk, pembuat):
    """Positif: urutan dari waktu dibuat paling baru."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="Lama", created_at=AWAL)
    _sesi(db_session, produk, pembuat, label="Baru", created_at=AWAL + timedelta(hours=1))

    # Act
    baris = as_role(Role.ADMIN).get(URL).json()

    # Assert
    assert [item["label"] for item in baris] == ["Baru", "Lama"]


def test_produk_yang_dihapus_tetap_bernama(as_role, db_session, produk, pembuat):
    """Edge: produk dihapus lunak, riwayat sesinya tetap menyebut namanya."""
    # Arrange
    _sesi(db_session, produk, pembuat)
    produk.deleted_at = AWAL
    db_session.commit()

    # Act
    (baris,) = as_role(Role.ADMIN).get(URL).json()

    # Assert
    assert baris["product_name"] == "Produk Uji"


def test_tanpa_sesi_daftar_kosong(as_role):
    """Edge: belum ada pengukuran, hasilnya daftar kosong, bukan galat."""
    # Act
    response = as_role(Role.ADMIN).get(URL)

    # Assert
    assert response.status_code == 200
    assert response.json() == []


def _jumlah_query(db_session, client) -> int:
    query: list[str] = []

    def catat(_conn, _cursor, statement, *_args):
        query.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", catat)
    try:
        assert client.get(URL).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", catat)
    return len(query)


def test_jumlah_query_tidak_bertambah_per_sesi(as_role, db_session, produk, pembuat):
    """Edge: satu sesi atau lima sesi, jumlah query sama (tanpa N+1)."""
    # Arrange
    client = as_role(Role.ADMIN)
    _mulai(db_session, _sesi(db_session, produk, pembuat), [AttemptStatus.PENDING] * 2)
    satu_sesi = _jumlah_query(db_session, client)
    for _ in range(4):
        _mulai(db_session, _sesi(db_session, produk, pembuat), [AttemptStatus.PENDING] * 2)

    # Act
    lima_sesi = _jumlah_query(db_session, client)

    # Assert
    assert lima_sesi == satu_sesi
