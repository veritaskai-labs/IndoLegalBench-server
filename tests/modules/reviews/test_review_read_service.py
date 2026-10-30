"""Unit test daftar tugas reviewer dan tampilan review kasus, dependency di-mock.

PBI-6 AC3, SCRUM-145, D3 langkah 16-19 dan catatan blind. Verdict yang
belum boleh dilihat dikembalikan sebagai null, bukan hanya disembunyikan
di tampilan.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases.schemas import ReviewCopy
from app.modules.reviews import service
from app.modules.reviews.models import (
    AssignmentStatus,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
from app.modules.reviews.visibility import Viewer
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.security import Role

SAYA = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REKAN = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
DIGANTI = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
PENULIS = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
WAKTU = datetime(2026, 10, 14, tzinfo=UTC)


def _verdict(decision, comment=None):
    return SimpleNamespace(decision=decision, comment=comment, submitted_at=WAKTU)


def _tugas(reviewer_id, ronde, verdict=None, status=AssignmentStatus.ACTIVE):
    return SimpleNamespace(
        id=uuid.uuid4(),
        reviewer_id=reviewer_id,
        review_round=ronde,
        status=status,
        verdict=verdict,
        assigned_at=WAKTU,
    )


@pytest.fixture
def dep():
    ronde = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=KASUS,
        case_version_id=VERSI,
        round_no=1,
        status=RoundStatus.OPEN,
        decision=None,
        opened_at=WAKTU,
        decided_at=None,
    )
    milik_saya = _tugas(SAYA, ronde, _verdict(VerdictDecision.APPROVE))
    milik_rekan = _tugas(REKAN, ronde, _verdict(VerdictDecision.REVISE, "Pasal salah"))
    diganti = _tugas(
        DIGANTI, ronde, _verdict(VerdictDecision.REVISE, "x"), AssignmentStatus.REPLACED
    )
    salinan = ReviewCopy(
        case_id=KASUS,
        case_code="PHK-001",
        author_id=PENULIS,
        case_version_id=VERSI,
        version_no=1,
        split_tag="dev",
        identity={"title": "PHK sepihak", "question": "Apakah PHK tanpa surat sah?"},
        legal_refs=[],
        answer_criteria={},
        traps=[],
    )
    with (
        patch.object(service, "repository") as repo,
        patch.object(service, "_cases") as cases,
        patch.object(service, "auth_service") as auth,
    ):
        repo.latest_round_for_case.return_value = ronde
        repo.assignments_in_round.return_value = [milik_saya, milik_rekan, diganti]
        repo.case_ids_assigned_to.return_value = [KASUS]
        repo.open_assignments_for.return_value = [milik_saya]
        cases.return_value.case_author.return_value = PENULIS
        cases.return_value.review_copies.return_value = {VERSI: salinan}
        auth.user_names.return_value = {SAYA: "Sari", REKAN: "Rudi"}
        yield SimpleNamespace(repo=repo, ronde=ronde, salinan=salinan, cases=cases.return_value)


def _verdicts(hasil) -> dict:
    return {slot.reviewer.id: slot.verdict for slot in hasil.reviewers}


# --- GET /cases/{id}/review -----------------------------------------------------------


def test_reviewer_hanya_melihat_verdictnya_sendiri_selama_round_open(dep):
    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(SAYA, Role.CONTRIBUTOR))

    # Assert
    terlihat = _verdicts(hasil)
    assert terlihat[SAYA].decision == VerdictDecision.APPROVE
    assert terlihat[REKAN] is None


def test_assignment_yang_diganti_tidak_ditampilkan(dep):
    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(SAYA, Role.ADMIN))

    # Assert
    assert set(_verdicts(hasil)) == {SAYA, REKAN}


def test_admin_melihat_semua_verdict_selama_round_open(dep):
    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(uuid.uuid4(), Role.ADMIN))

    # Assert
    assert _verdicts(hasil)[REKAN].comment == "Pasal salah"


def test_penulis_belum_melihat_verdict_selama_round_open(dep):
    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(PENULIS, Role.CONTRIBUTOR))

    # Assert
    assert set(_verdicts(hasil).values()) == {None}


def test_semua_terbuka_setelah_round_diputuskan(dep):
    # Arrange
    dep.ronde.status = RoundStatus.DECIDED
    dep.ronde.decision = RoundDecision.REVISE

    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(PENULIS, Role.CONTRIBUTOR))

    # Assert
    assert _verdicts(hasil)[REKAN].comment == "Pasal salah"
    assert hasil.decision == RoundDecision.REVISE


def test_nama_reviewer_ikut_ditampilkan(dep):
    # Act
    hasil = service.get_case_review(MagicMock(), KASUS, Viewer(SAYA, Role.CONTRIBUTOR))

    # Assert
    assert {slot.reviewer.name for slot in hasil.reviewers} == {"Sari", "Rudi"}


@pytest.mark.parametrize(
    "viewer",
    [Viewer(uuid.uuid4(), Role.CONTRIBUTOR), Viewer(uuid.uuid4(), Role.VIEWER)],
    ids=["kontributor-lain", "viewer"],
)
def test_yang_tidak_terlibat_ditolak(dep, viewer):
    """Bukan penulis dan tidak pernah ditugaskan di kasus ini."""
    # Arrange
    dep.repo.case_ids_assigned_to.return_value = []

    # Act + Assert
    with pytest.raises(ForbiddenError):
        service.get_case_review(MagicMock(), KASUS, viewer)


def test_reviewer_yang_tidak_pernah_ditugaskan_ditolak(dep):
    # Arrange
    dep.repo.case_ids_assigned_to.return_value = []

    # Act + Assert
    with pytest.raises(ForbiddenError):
        service.get_case_review(MagicMock(), KASUS, Viewer(SAYA, Role.CONTRIBUTOR))


def test_kasus_belum_pernah_diajukan(dep):
    # Arrange
    dep.repo.latest_round_for_case.return_value = None

    # Act
    with pytest.raises(NotFoundError) as info:
        service.get_case_review(MagicMock(), KASUS, Viewer(PENULIS, Role.CONTRIBUTOR))

    # Assert
    assert info.value.code == "NO_REVIEW_ROUND"


# --- GET /reviews/assigned ---------------------------------------------------------


def test_daftar_tugas_memuat_isi_kasus_dan_verdict_sendiri(dep):
    # Arrange
    db = MagicMock()

    # Act
    [baris] = service.list_assigned(db, SAYA)

    # Assert
    dep.repo.open_assignments_for.assert_called_once_with(db, SAYA)
    dep.cases.review_copies.assert_called_once_with(db, {VERSI})
    assert baris.case == dep.salinan
    assert baris.round_no == 1
    assert baris.my_verdict.decision == VerdictDecision.APPROVE


def test_daftar_tugas_kosong_tidak_membaca_kasus(dep):
    # Arrange
    dep.repo.open_assignments_for.return_value = []
    dep.cases.review_copies.return_value = {}

    # Act
    hasil = service.list_assigned(MagicMock(), SAYA)

    # Assert
    assert hasil == []
