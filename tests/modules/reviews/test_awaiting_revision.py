"""SCRUM-242: antrean "Menunggu revisi penulis" untuk reviewer.

Repository diuji di SQLite. Service diuji dengan repository dan modul cases
di-mock, jadi tanpa database.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.reviews import repository, service
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)

KASUS = uuid.UUID("00000000-0000-0000-0000-0000000242d1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000242e1")
REVIEWER_A = uuid.UUID("00000000-0000-0000-0000-0000000242a1")
REVIEWER_B = uuid.UUID("00000000-0000-0000-0000-0000000242a2")
APPROVE, REVISE = VerdictDecision.APPROVE, VerdictDecision.REVISE


def _round(db, *, round_no=1, status=RoundStatus.DECIDED, decision=RoundDecision.REVISE):
    ronde = ReviewRound(
        case_id=KASUS,
        case_version_id=VERSI,
        round_no=round_no,
        status=status,
        decision=decision,
        decided_at=datetime(2026, 10, 1, tzinfo=UTC) if decision else None,
    )
    db.add(ronde)
    db.flush()
    return ronde


def _tugas(db, ronde, reviewer_id, verdict=None, comment=None, status=AssignmentStatus.ACTIVE):
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=reviewer_id, status=status)
    db.add(tugas)
    db.flush()
    if verdict is not None:
        db.add(ReviewVerdict(assignment_id=tugas.id, decision=verdict, comment=comment))
        db.flush()
    return tugas


# --- Repository ----------------------------------------------------------------


def test_reviewer_yang_meminta_revisi_masuk_antrean(db_session):
    # Arrange
    ronde = _round(db_session)
    milik_a = _tugas(db_session, ronde, REVIEWER_A, REVISE, "Perjelas rujukan")
    _tugas(db_session, ronde, REVIEWER_B, APPROVE)

    # Act
    hasil = repository.awaiting_revision_assignments(db_session, REVIEWER_A)

    # Assert
    assert [t.id for t in hasil] == [milik_a.id]


def test_reviewer_yang_setuju_masuk_antrean_selama_rekan_meminta_revisi(db_session):
    # Arrange
    ronde = _round(db_session)
    _tugas(db_session, ronde, REVIEWER_A, REVISE, "Perjelas rujukan")
    milik_b = _tugas(db_session, ronde, REVIEWER_B, APPROVE)

    # Act
    hasil = repository.awaiting_revision_assignments(db_session, REVIEWER_B)

    # Assert
    assert [t.id for t in hasil] == [milik_b.id]


def test_round_disetujui_tidak_masuk_antrean(db_session):
    # Arrange
    ronde = _round(db_session, decision=RoundDecision.APPROVED)
    _tugas(db_session, ronde, REVIEWER_A, APPROVE)

    # Act + Assert
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_A) == []


def test_round_yang_belum_diputuskan_tidak_masuk_antrean(db_session):
    # Arrange
    ronde = _round(db_session, status=RoundStatus.OPEN, decision=None)
    _tugas(db_session, ronde, REVIEWER_A, REVISE, "Perjelas rujukan")

    # Act + Assert
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_A) == []


def test_kasus_yang_sudah_diajukan_ulang_keluar_dari_antrean(db_session):
    # Arrange: round 1 revise, lalu Author mengajukan ulang sehingga round 2 dibuka
    ronde_1 = _round(db_session, round_no=1)
    _tugas(db_session, ronde_1, REVIEWER_A, REVISE, "Perjelas rujukan")
    _round(db_session, round_no=2, status=RoundStatus.OPEN, decision=None)

    # Act + Assert
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_A) == []


def test_assignment_replaced_dan_void_tidak_masuk_antrean(db_session):
    # Arrange
    ronde = _round(db_session)
    _tugas(db_session, ronde, REVIEWER_A, REVISE, "x", status=AssignmentStatus.REPLACED)
    _tugas(db_session, ronde, REVIEWER_B, REVISE, "y", status=AssignmentStatus.VOID)

    # Act + Assert
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_A) == []
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_B) == []


def test_antrean_hanya_memuat_assignment_milik_reviewer_itu(db_session):
    # Arrange
    ronde = _round(db_session)
    _tugas(db_session, ronde, REVIEWER_B, REVISE, "Perjelas rujukan")

    # Act + Assert
    assert repository.awaiting_revision_assignments(db_session, REVIEWER_A) == []


def test_id_thread_hanya_assignment_aktif_yang_punya_komentar(db_session):
    # Arrange
    ronde = _round(db_session)
    dengan_komentar = _tugas(db_session, ronde, REVIEWER_A, REVISE, "Perjelas rujukan")
    _tugas(db_session, ronde, REVIEWER_B, APPROVE, "   ")
    diganti = _tugas(db_session, ronde, uuid.uuid4(), REVISE, "Lama", AssignmentStatus.REPLACED)

    # Act
    hasil = repository.thread_assignment_ids(db_session, {ronde.id})

    # Assert
    assert hasil == {ronde.id: [dengan_komentar.id]}
    assert diganti.id not in hasil[ronde.id]


def test_id_thread_kosong_untuk_himpunan_round_kosong(db_session):
    # Act + Assert
    assert repository.thread_assignment_ids(db_session, set()) == {}


# --- Service (dependency di-mock) -----------------------------------------------


def _tugas_palsu(decision=REVISE):
    ronde = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        round_no=2,
        decided_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        review_round=ronde,
        verdict=SimpleNamespace(decision=decision),
    )


@patch("app.modules.reviews.service._cases")
@patch("app.modules.reviews.service.repository")
def test_service_memetakan_assignment_ke_item_antrean(repo, cases):
    # Arrange
    tugas = _tugas_palsu(APPROVE)
    peer_id = uuid.uuid4()
    repo.awaiting_revision_assignments.return_value = [tugas]
    repo.thread_assignment_ids.return_value = {tugas.review_round.id: [peer_id]}
    cases.return_value.review_copies.return_value = {
        tugas.review_round.case_version_id: SimpleNamespace(
            case_code="K-007", identity=SimpleNamespace(title="Sengketa sewa")
        )
    }
    db = MagicMock()

    # Act
    [item] = service.list_awaiting_revision(db, REVIEWER_B)

    # Assert
    assert item.assignment_id == tugas.id
    assert item.case_id == tugas.review_round.case_id
    assert item.case_code == "K-007"
    assert item.title == "Sengketa sewa"
    assert item.round_no == 2
    assert item.my_decision == APPROVE
    assert item.decided_at == tugas.review_round.decided_at
    assert item.thread_assignment_ids == [peer_id]
    repo.awaiting_revision_assignments.assert_called_once_with(db, REVIEWER_B)


@patch("app.modules.reviews.service._cases")
@patch("app.modules.reviews.service.repository")
def test_service_mengembalikan_daftar_kosong_bila_tidak_ada_antrean(repo, cases):
    # Arrange
    repo.awaiting_revision_assignments.return_value = []
    repo.thread_assignment_ids.return_value = {}
    cases.return_value.review_copies.return_value = {}

    # Act
    hasil = service.list_awaiting_revision(MagicMock(), REVIEWER_A)

    # Assert
    assert hasil == []
