"""Service get_open_version. SCRUM-172.

Repository di-mock, jadi test ini tidak menyentuh database. Yang diuji:
siapa yang boleh membaca draf dan versi mana yang dianggap terbuka.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.shared.exceptions import ForbiddenError, NotFoundError

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-00000000a000")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000b000")
WAKTU = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)


def _versi(version_no: int, status: CaseStatus, judul: str) -> CaseVersion:
    return CaseVersion(
        id=uuid.uuid4(),
        version_no=version_no,
        status=status,
        case_code="PHK-001",
        split_tag=SplitTag.DEV,
        content={"title": judul, "question": "Apakah PHK tanpa surat sah?"},
        created_at=WAKTU,
        updated_at=WAKTU,
    )


def _kasus(*, current: CaseVersion, approved: CaseVersion | None) -> Case:
    kasus = Case(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        case_code="PHK-001",
        created_by=PEMBUAT,
        updated_by=PEMBUAT,
    )
    kasus.current_version = current
    kasus.current_version_id = current.id
    kasus.latest_approved_version = approved
    kasus.latest_approved_version_id = approved.id if approved else None
    return kasus


@pytest.fixture
def repo():
    with patch.object(service, "repository", autospec=True) as mocked:
        yield mocked


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.IN_REVIEW, CaseStatus.NEEDS_REVISION]
)
def test_pembuat_mendapat_versi_terbuka_bukan_yang_disetujui(repo, status):
    # Arrange
    db, case_id = MagicMock(), uuid.uuid4()
    approved = _versi(1, CaseStatus.APPROVED, "Judul disetujui")
    terbuka = _versi(2, status, "Judul draf")
    repo.get_by_id.return_value = _kasus(current=terbuka, approved=approved)

    # Act
    hasil = service.get_open_version(db, case_id, actor_id=PEMBUAT, is_admin=False)

    # Assert
    repo.get_by_id.assert_called_once_with(db, case_id)
    assert hasil.version == 2
    assert hasil.status == status
    assert hasil.identity.title == "Judul draf"


def test_admin_boleh_membaca_draf_milik_orang_lain(repo):
    # Arrange
    approved = _versi(1, CaseStatus.APPROVED, "Judul disetujui")
    draf = _versi(2, CaseStatus.DRAFT, "Judul draf")
    repo.get_by_id.return_value = _kasus(current=draf, approved=approved)

    # Act
    hasil = service.get_open_version(MagicMock(), uuid.uuid4(), actor_id=ORANG_LAIN, is_admin=True)

    # Assert
    assert hasil.version == 2
    assert hasil.status == CaseStatus.DRAFT
    assert hasil.identity.title == "Judul draf"


def test_author_lain_ditolak_dan_isi_draf_tidak_bocor(repo):
    # Arrange
    repo.get_by_id.return_value = _kasus(
        current=_versi(2, CaseStatus.DRAFT, "Judul draf"),
        approved=_versi(1, CaseStatus.APPROVED, "Judul disetujui"),
    )

    # Act
    with pytest.raises(ForbiddenError) as galat:
        service.get_open_version(MagicMock(), uuid.uuid4(), actor_id=ORANG_LAIN, is_admin=False)

    # Assert
    assert galat.value.code == ForbiddenError.code
    assert "Judul draf" not in galat.value.message


def test_tanpa_versi_terbuka_menjawab_not_found_dengan_kode_khusus(repo):
    # Arrange
    approved = _versi(1, CaseStatus.APPROVED, "Judul disetujui")
    repo.get_by_id.return_value = _kasus(current=approved, approved=approved)

    # Act
    with pytest.raises(NotFoundError) as galat:
        service.get_open_version(MagicMock(), uuid.uuid4(), actor_id=PEMBUAT, is_admin=False)

    # Assert
    assert galat.value.code == "NO_OPEN_VERSION"


def test_kasus_tidak_ada_menjawab_not_found_biasa(repo):
    # Arrange
    repo.get_by_id.return_value = None

    # Act
    with pytest.raises(NotFoundError) as galat:
        service.get_open_version(MagicMock(), uuid.uuid4(), actor_id=PEMBUAT, is_admin=False)

    # Assert
    assert galat.value.code == NotFoundError.code


def test_pemeriksaan_akses_didahulukan_sebelum_status_versi(repo):
    # Arrange: kasus tanpa draf, dibaca orang yang bukan pembuat
    approved = _versi(1, CaseStatus.APPROVED, "Judul disetujui")
    repo.get_by_id.return_value = _kasus(current=approved, approved=approved)

    # Act
    with pytest.raises(ForbiddenError) as galat:
        service.get_open_version(MagicMock(), uuid.uuid4(), actor_id=ORANG_LAIN, is_admin=False)

    # Assert: 403, bukan 404 NO_OPEN_VERSION, supaya status draf tidak terbaca
    assert galat.value.code == ForbiddenError.code
