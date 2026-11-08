"""GET /manual-measurements: daftar sesi pengukuran manual untuk Admin dan Kontributor.

PBI-22 (SCRUM-176), SCRUM-230, AC8, AC11.
AC11: Semua Admin dan Kontributor dapat melihat sesi manual milik orang lain.
Peran Developer AI dan Viewer ditolak 403.
Hanya sesi berjenis manual yang tampil di endpoint ini.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, MeasurementType
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
from tests.modules.cases.test_cases import ADMIN_LAIN
from tests.modules.providers.test_product_lists import _otomatis

URL = "/manual-measurements"
AWAL = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def pembuat_admin(db_session) -> User:
    user = User(email="admin-manual-list@veritask.test", name="Admin Manual", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def pembuat_kontributor(db_session) -> User:
    user = User(
        email="kontributor-manual-list@veritask.test",
        name="Kontributor Manual",
        role=Role.CONTRIBUTOR,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk_manual(db_session) -> AiProduct:
    produk = AiProduct(
        name="LegalHero Pro",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="LegalHero",
        site_url="https://legalhero.id",
        manual_params="Premium",
        is_active=True,
        created_by=ADMIN_LAIN,
    )
    db_session.add(produk)
    db_session.commit()
    return produk


@pytest.fixture
def produk_otomatis(db_session) -> AiProduct:
    item = _otomatis("DeepSeek Auto")
    db_session.add(item)
    db_session.commit()
    return item


def _sesi_manual(db_session, produk, pembuat, **ubah) -> MeasurementSession:
    """Satu sesi manual sintetis."""
    isi = {
        "label": "Sesi Manual Uji",
        "purpose": SessionPurpose.OFFICIAL,
        "session_type": SessionType.MANUAL,
        "product_id": produk.id,
        "repetitions": 1,
        "split_filter": SplitFilter.BOTH,
        "created_by": pembuat.id,
        "created_at": AWAL,
        "status": SessionStatus.NOT_FILLED,
    }
    isi.update(ubah)
    sesi = MeasurementSession(**isi)
    db_session.add(sesi)
    db_session.commit()
    return sesi


def _sesi_otomatis(db_session, produk, pembuat, **ubah) -> MeasurementSession:
    """Satu sesi otomatis sintetis."""
    isi = {
        "label": "Sesi Otomatis Uji",
        "purpose": SessionPurpose.TRIAL,
        "session_type": SessionType.AUTOMATIC,
        "product_id": produk.id,
        "repetitions": 2,
        "split_filter": SplitFilter.BOTH,
        "created_by": pembuat.id,
        "created_at": AWAL,
        "status": SessionStatus.QUEUED,
    }
    isi.update(ubah)
    sesi = MeasurementSession(**isi)
    db_session.add(sesi)
    db_session.commit()
    return sesi


def _tambah_snapshot_dan_attempt(
    db_session,
    sesi: MeasurementSession,
    status: AttemptStatus = AttemptStatus.PENDING,
    filled: bool = False,
) -> None:
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={"split_tag": "dev"},
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item.id,
            repetition_no=1,
            source=AttemptSource.MANUAL,
            status=status,
            filled_at=AWAL if filled else None,
        )
    )
    db_session.commit()


def test_contributor_boleh_membuka_daftar_sesi_manual(
    as_role, db_session, produk_manual, pembuat_kontributor
):
    """Positif: AC11, Kontributor boleh membuka daftar sesi pengukuran manual."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_kontributor, label="Sesi Kontributor")

    # Act
    respons = as_role(Role.CONTRIBUTOR).get(URL)

    # Assert
    assert respons.status_code == 200
    baris = respons.json()
    assert len(baris) == 1
    assert baris[0]["label"] == "Sesi Kontributor"
    assert baris[0]["status"] == "not_filled"


def test_admin_boleh_membuka_daftar_sesi_manual(as_role, db_session, produk_manual, pembuat_admin):
    """Positif: AC8, Admin boleh membuka daftar sesi pengukuran manual."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Sesi Admin")

    # Act
    respons = as_role(Role.ADMIN).get(URL)

    # Assert
    assert respons.status_code == 200
    baris = respons.json()
    assert len(baris) == 1
    assert baris[0]["label"] == "Sesi Admin"


def test_kontributor_melihat_sesi_manual_buatan_orang_lain(
    as_role, db_session, produk_manual, pembuat_admin
):
    """Positif: AC11, Kontributor dapat melihat sesi manual milik orang lain (mis. Admin)."""
    # Arrange: dibuat oleh Admin
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Sesi Milik Admin")

    # Act: diakses oleh Kontributor
    respons = as_role(Role.CONTRIBUTOR).get(URL)

    # Assert
    assert respons.status_code == 200
    baris = respons.json()
    assert len(baris) == 1
    assert baris[0]["label"] == "Sesi Milik Admin"
    assert baris[0]["created_by_name"] == "Admin Manual"


def test_daftar_manual_hanya_memuat_sesi_manual(
    as_role, db_session, produk_manual, produk_otomatis, pembuat_admin
):
    """Positif: GET /manual-measurements tidak memuat sesi otomatis."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Manual Saja")
    _sesi_otomatis(db_session, produk_otomatis, pembuat_admin, label="Otomatis Saja")

    # Act
    respons = as_role(Role.CONTRIBUTOR).get(URL)

    # Assert
    assert respons.status_code == 200
    labels = [item["label"] for item in respons.json()]
    assert labels == ["Manual Saja"]


def test_daftar_sesi_manual_terbaru_di_atas(as_role, db_session, produk_manual, pembuat_admin):
    """Positif: urutan sesi manual terbaru di atas (created_at desc)."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Lama", created_at=AWAL)
    _sesi_manual(
        db_session, produk_manual, pembuat_admin, label="Baru", created_at=AWAL + timedelta(hours=1)
    )

    # Act
    baris = as_role(Role.CONTRIBUTOR).get(URL).json()

    # Assert
    assert [item["label"] for item in baris] == ["Baru", "Lama"]


def test_sesi_manual_yang_diarsipkan_tidak_tampil_di_daftar_utama(
    as_role, db_session, produk_manual, pembuat_admin
):
    """Edge: sesi manual yang diarsipkan tidak tampil di daftar utama (archived=false)."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Aktif")
    _sesi_manual(
        db_session,
        produk_manual,
        pembuat_admin,
        label="Arsip",
        status=SessionStatus.FINALIZED,
        archived_at=AWAL,
        archived_by=pembuat_admin.id,
    )

    # Act
    baris = as_role(Role.CONTRIBUTOR).get(URL).json()

    # Assert
    assert [item["label"] for item in baris] == ["Aktif"]


def test_daftar_arsip_manual_hanya_memuat_sesi_arsip(
    as_role, db_session, produk_manual, pembuat_admin
):
    """Positif: archived=true menampilkan sesi manual yang diarsipkan."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Aktif")
    _sesi_manual(
        db_session,
        produk_manual,
        pembuat_admin,
        label="Arsip",
        status=SessionStatus.FINALIZED,
        archived_at=AWAL,
        archived_by=pembuat_admin.id,
    )

    # Act
    baris = as_role(Role.CONTRIBUTOR).get(f"{URL}?archived=true").json()

    # Assert
    assert [item["label"] for item in baris] == ["Arsip"]


@pytest.mark.parametrize("role", [Role.DEVELOPER_AI, Role.VIEWER])
def test_peran_selain_admin_dan_kontributor_ditolak_403(as_role, role):
    """Negatif: Developer AI dan Viewer ditolak 403 pada daftar sesi manual."""
    # Act
    response = as_role(role).get(URL)

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login ditolak 401 UNAUTHENTICATED."""
    # Act
    response = client.get(URL)

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_tanpa_sesi_manual_daftar_kosong(as_role):
    """Edge: belum ada sesi manual, hasilnya daftar kosong [] dengan 200 OK."""
    # Act
    response = as_role(Role.CONTRIBUTOR).get(URL)

    # Assert
    assert response.status_code == 200
    assert response.json() == []


def test_produk_manual_yang_dihapus_tetap_bernama(
    as_role, db_session, produk_manual, pembuat_admin
):
    """Edge: produk manual soft-deleted, riwayat sesinya tetap memuat nama produk."""
    # Arrange
    _sesi_manual(db_session, produk_manual, pembuat_admin, label="Sesi Riwayat")
    produk_manual.deleted_at = AWAL
    db_session.commit()

    # Act
    (baris,) = as_role(Role.CONTRIBUTOR).get(URL).json()

    # Assert
    assert baris["product_name"] == "LegalHero Pro"


def test_progres_sesi_manual_terisi(as_role, db_session, produk_manual, pembuat_admin):
    """Positif: sesi manual yang sudah punya jawaban terisi menunjukkan progress completed."""
    # Arrange
    sesi = _sesi_manual(
        db_session, produk_manual, pembuat_admin, label="Sesi Terisi", status=SessionStatus.COMPLETE
    )
    _tambah_snapshot_dan_attempt(db_session, sesi, filled=True)

    # Act
    (baris,) = as_role(Role.CONTRIBUTOR).get(URL).json()

    # Assert
    assert baris["case_count"] == 1
    assert baris["progress"]["completed"] == 1
    assert baris["progress"]["total"] == 1
    assert baris["progress"]["completed_cases"] == 1
    assert baris["progress"]["total_cases"] == 1
    assert baris["progress"]["percent_cases_complete"] == 100
