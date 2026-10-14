"""Unit test reviews.service.submit_verdict, dependency di-mock.

PBI-6 AC2-AC6, SCRUM-145, D3 langkah 21-41. Alur lewat HTTP ada di
test_review_api.py, keputusan bersamaan di PostgreSQL ada di
tests/integration/test_review_concurrency_pg.py.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.reviews import service
from app.modules.reviews.models import (
    AssignmentStatus,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError

APPROVE, REVISE = VerdictDecision.APPROVE, VerdictDecision.REVISE
SAYA = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REKAN = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
PENULIS = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
TUGAS = uuid.UUID("00000000-0000-0000-0000-0000000000e1")


def _verdict(decision, comment=None):
    return SimpleNamespace(decision=decision, comment=comment, submitted_at=None)


@pytest.fixture
def dep():
    ronde = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=KASUS,
        case_version_id=VERSI,
        status=RoundStatus.OPEN,
        decision=None,
        decided_at=None,
    )
    tugas = SimpleNamespace(
        id=TUGAS,
        reviewer_id=SAYA,
        review_round_id=ronde.id,
        round=ronde,
        status=AssignmentStatus.ACTIVE,
        verdict=None,
    )
    rekan = SimpleNamespace(reviewer_id=REKAN, status=AssignmentStatus.ACTIVE, verdict=None)
    salinan = SimpleNamespace(case_id=KASUS, case_code="PHK-001", author_id=PENULIS)
    notifier = MagicMock()
    with (
        patch.object(service, "repository") as repo,
        patch.object(service, "_cases") as cases,
    ):
        repo.get_assignment.return_value = tugas
        repo.lock_round.return_value = ronde
        repo.add_verdict.side_effect = lambda db, verdict: setattr(tugas, "verdict", verdict)
        repo.assignments_in_round.side_effect = lambda db, round_id: [tugas, rekan]
        cases.return_value.review_copies.return_value = {VERSI: salinan}
        yield SimpleNamespace(
            repo=repo,
            cases=cases.return_value,
            ronde=ronde,
            tugas=tugas,
            rekan=rekan,
            notifier=notifier,
        )


def _kirim(dep, decision=APPROVE, comment=None, db=None, reviewer_id=SAYA):
    return service.submit_verdict(
        db or MagicMock(),
        TUGAS,
        reviewer_id=reviewer_id,
        decision=decision,
        comment=comment,
        notifier=dep.notifier,
    )


# --- Penilaian pertama ------------------------------------------------------------


def test_verdict_pertama_tersimpan_round_tetap_open(dep):
    # Arrange
    db = MagicMock()

    # Act
    hasil = _kirim(dep, db=db)

    # Assert
    assert dep.tugas.verdict.decision == APPROVE
    assert hasil.decision == APPROVE
    assert hasil.round_status == RoundStatus.OPEN
    assert dep.ronde.decision is None
    dep.cases.apply_review_decision.assert_not_called()
    db.commit.assert_called_once()
    dep.notifier.notify.assert_not_called()


def test_round_dikunci_sebelum_menghitung(dep):
    # Arrange
    db = MagicMock()

    # Act
    _kirim(dep, db=db)

    # Assert
    dep.repo.lock_round.assert_called_once_with(db, dep.ronde.id)


def test_komentar_dirapikan_dan_kosong_jadi_none(dep):
    # Act
    _kirim(dep, comment="   ")

    # Assert
    assert dep.tugas.verdict.comment is None


@pytest.mark.parametrize("komentar", [None, "", "   "])
def test_revise_tanpa_komentar_ditolak(dep, komentar):
    # Act
    with pytest.raises(ValidationError) as info:
        _kirim(dep, REVISE, komentar)

    # Assert
    assert info.value.code == "COMMENT_REQUIRED"
    assert info.value.field == "comment"
    dep.repo.add_verdict.assert_not_called()


# --- Keputusan (AC4-AC6) -------------------------------------------------------------


def test_dua_approve_menyetujui_versi(dep):
    # Arrange
    db = MagicMock()
    dep.rekan.verdict = _verdict(APPROVE)

    # Act
    hasil = _kirim(dep, db=db)

    # Assert
    assert dep.ronde.status == RoundStatus.DECIDED
    assert dep.ronde.decision == RoundDecision.APPROVED
    assert dep.ronde.decided_at is not None
    dep.cases.apply_review_decision.assert_called_once_with(db, VERSI, approved=True)
    assert hasil.round_status == RoundStatus.DECIDED
    assert hasil.round_decision == RoundDecision.APPROVED


def test_satu_revise_mengembalikan_ke_penulis(dep):
    # Arrange
    db = MagicMock()
    dep.rekan.verdict = _verdict(APPROVE)

    # Act
    _kirim(dep, REVISE, "Pasal 151 sudah diubah UU Cipta Kerja", db=db)

    # Assert
    assert dep.ronde.decision == RoundDecision.REVISE
    dep.cases.apply_review_decision.assert_called_once_with(db, VERSI, approved=False)


def test_penulis_diberi_tahu_setelah_commit_beserta_komentar(dep):
    """AC5 dan D3 langkah 39-40: notifikasi dikirim setelah COMMIT."""
    # Arrange
    urutan = []
    db = MagicMock()
    db.commit.side_effect = lambda: urutan.append("commit")
    dep.notifier.notify.side_effect = lambda *args: urutan.append("notify")
    dep.rekan.verdict = _verdict(REVISE, "Jebakan kurang jelas")

    # Act
    _kirim(dep, REVISE, "Pasal salah", db=db)

    # Assert
    assert urutan == ["commit", "notify"]
    user_ids, jenis, payload = dep.notifier.notify.call_args.args
    assert user_ids == [PENULIS]
    assert jenis == "review_decided"
    assert "perlu revisi" in payload["title"]
    assert "Pasal salah" in payload["body"]
    assert "Jebakan kurang jelas" in payload["body"]
    assert payload["link"] == f"/cases/{KASUS}"


def test_penulis_diberi_tahu_saat_disetujui(dep):
    # Arrange
    dep.rekan.verdict = _verdict(APPROVE)

    # Act
    _kirim(dep)

    # Assert
    _, _, payload = dep.notifier.notify.call_args.args
    assert "disetujui" in payload["title"]


def test_verdict_assignment_yang_diganti_tidak_ikut_dihitung(dep):
    """AC7: verdict rekan yang sudah replaced tidak membuat round diputuskan."""
    # Arrange
    dep.rekan.verdict = _verdict(APPROVE)
    dep.rekan.status = AssignmentStatus.REPLACED

    # Act
    hasil = _kirim(dep)

    # Assert
    assert hasil.round_status == RoundStatus.OPEN
    dep.cases.apply_review_decision.assert_not_called()


# --- Penolakan (AC2, ticket SCRUM-145) ------------------------------------------------


def test_assignment_tidak_dikenal(dep):
    # Arrange
    dep.repo.get_assignment.return_value = None

    # Act + Assert
    with pytest.raises(NotFoundError):
        _kirim(dep)


def test_assignment_milik_reviewer_lain(dep):
    # Act
    with pytest.raises(ForbiddenError) as info:
        _kirim(dep, reviewer_id=REKAN)

    # Assert
    assert info.value.code == "NOT_YOUR_ASSIGNMENT"
    dep.repo.lock_round.assert_not_called()


def test_reviewer_yang_juga_penulis_ditolak(dep):
    # Arrange
    dep.cases.review_copies.return_value[VERSI].author_id = SAYA

    # Act
    with pytest.raises(ForbiddenError) as info:
        _kirim(dep)

    # Assert
    assert info.value.code == "REVIEWER_IS_AUTHOR"
    dep.repo.add_verdict.assert_not_called()


@pytest.mark.parametrize("status", [AssignmentStatus.REPLACED, AssignmentStatus.VOID])
def test_assignment_tidak_aktif_ditolak(dep, status):
    # Arrange
    dep.tugas.status = status

    # Act
    with pytest.raises(ConflictError) as info:
        _kirim(dep)

    # Assert
    assert info.value.code == "ASSIGNMENT_NOT_ACTIVE"


@pytest.mark.parametrize("status", [RoundStatus.AWAITING_ASSIGNMENT, RoundStatus.DECIDED])
def test_round_yang_tidak_open_ditolak(dep, status):
    # Arrange
    dep.ronde.status = status

    # Act
    with pytest.raises(ConflictError) as info:
        _kirim(dep)

    # Assert
    assert info.value.code == "ROUND_NOT_OPEN"


def test_verdict_kedua_dari_assignment_yang_sama_ditolak(dep):
    # Arrange
    dep.tugas.verdict = _verdict(APPROVE)

    # Act
    with pytest.raises(ConflictError) as info:
        _kirim(dep)

    # Assert
    assert info.value.code == "VERDICT_ALREADY_SUBMITTED"


def test_bentrok_uq_verdict_dibatalkan_jadi_konflik(dep):
    # Arrange
    db = MagicMock()
    dep.repo.add_verdict.side_effect = IntegrityError("insert", {}, Exception("uq"))

    # Act
    with pytest.raises(ConflictError) as info:
        _kirim(dep, db=db)

    # Assert
    assert info.value.code == "VERDICT_ALREADY_SUBMITTED"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
