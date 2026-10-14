"""Test tabel review_rounds, review_assignments, review_verdicts.

PBI-6, SCRUM-143. Bentuk tabel mengikuti D4a (ERD Versioning Snapshot):
satu round per pengajuan versi, assignment per reviewer, dan satu verdict
per assignment. Aturan yang dijaga database diuji di sini, aturan yang
dijaga service diuji di test_review_service.py.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)
from app.shared.database import Base

KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
REVIEWER_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REVIEWER_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


def _round(db_session, *, round_no: int = 1, **kolom) -> ReviewRound:
    baris = ReviewRound(case_id=KASUS, case_version_id=VERSI, round_no=round_no, **kolom)
    db_session.add(baris)
    db_session.commit()
    return baris


def _assignment(db_session, round_id: uuid.UUID, reviewer_id: uuid.UUID, **kolom):
    baris = ReviewAssignment(review_round_id=round_id, reviewer_id=reviewer_id, **kolom)
    db_session.add(baris)
    db_session.commit()
    return baris


@pytest.mark.parametrize("tabel", ["review_rounds", "review_assignments", "review_verdicts"])
def test_tabel_terdaftar_di_metadata(tabel):
    assert tabel in Base.metadata.tables


def test_round_baru_menunggu_penugasan_dan_waktu_buka_terisi(db_session):
    # Act
    baris = _round(db_session)

    # Assert
    assert baris.status == RoundStatus.AWAITING_ASSIGNMENT
    assert baris.decision is None
    assert baris.opened_at is not None
    assert baris.decided_at is None


def test_nomor_round_tidak_boleh_ganda_untuk_versi_yang_sama(db_session):
    # Arrange
    _round(db_session, round_no=1)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _round(db_session, round_no=1)


def test_round_kedua_untuk_versi_yang_sama_boleh(db_session):
    # Arrange
    _round(db_session, round_no=1)

    # Act
    kedua = _round(db_session, round_no=2)

    # Assert
    assert kedua.round_no == 2


def test_assignment_bawaan_aktif_dari_sistem(db_session):
    # Arrange
    ronde = _round(db_session)

    # Act
    baris = _assignment(db_session, ronde.id, REVIEWER_A)

    # Assert
    assert baris.status == AssignmentStatus.ACTIVE
    assert baris.source == AssignmentSource.SYSTEM
    assert baris.assigned_by is None
    assert baris.assigned_at is not None
    assert baris.ended_at is None


def test_reviewer_tidak_boleh_aktif_dua_kali_di_round_yang_sama(db_session):
    # Arrange
    ronde = _round(db_session)
    _assignment(db_session, ronde.id, REVIEWER_A)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _assignment(db_session, ronde.id, REVIEWER_A)


def test_reviewer_yang_sudah_diganti_boleh_ditugaskan_lagi(db_session):
    """UQ parsial D4a hanya berlaku untuk assignment yang aktif."""
    # Arrange
    ronde = _round(db_session)
    _assignment(db_session, ronde.id, REVIEWER_A, status=AssignmentStatus.REPLACED)

    # Act
    baru = _assignment(db_session, ronde.id, REVIEWER_A)

    # Assert
    assert baru.status == AssignmentStatus.ACTIVE


def test_dua_reviewer_berbeda_boleh_aktif_di_round_yang_sama(db_session):
    # Arrange
    ronde = _round(db_session)
    _assignment(db_session, ronde.id, REVIEWER_A)

    # Act
    kedua = _assignment(db_session, ronde.id, REVIEWER_B)

    # Assert
    assert kedua.reviewer_id == REVIEWER_B


def test_verdict_approve_tanpa_komentar_boleh(db_session):
    # Arrange
    tugas = _assignment(db_session, _round(db_session).id, REVIEWER_A)

    # Act
    verdict = ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE)
    db_session.add(verdict)
    db_session.commit()

    # Assert
    assert verdict.submitted_at is not None
    assert verdict.carried_from_verdict_id is None


@pytest.mark.parametrize("komentar", [None, "", "   "])
def test_verdict_revise_wajib_berkomentar(db_session, komentar):
    """D4a: komentar wajib bila revise, dijaga juga oleh database."""
    # Arrange
    tugas = _assignment(db_session, _round(db_session).id, REVIEWER_A)

    # Act + Assert
    db_session.add(
        ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.REVISE, comment=komentar)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_satu_assignment_hanya_punya_satu_verdict(db_session):
    # Arrange
    tugas = _assignment(db_session, _round(db_session).id, REVIEWER_A)
    db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
    db_session.commit()

    # Act + Assert
    db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_enum_disimpan_sebagai_nilai_bukan_nama_member():
    # Arrange
    kolom = Base.metadata.tables["review_rounds"].c.status

    # Act + Assert
    assert list(kolom.type.enums) == ["awaiting_assignment", "open", "decided"]
