"""Pengajuan ulang setelah revisi. PBI-6 AC9, SCRUM-146.

Satu approval dibawa ke round berikutnya. Hanya reviewer yang meminta
revisi ditugaskan lagi. Komentar revisi di round lama tidak diubah.
"""

import uuid
from unittest.mock import patch

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases import service as cases_service
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.notifications.service import DbNotifier
from app.modules.reviews import service as reviews_service
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
from app.modules.suites.models import Suite, SuiteStatus
from app.shared import request_context
from app.shared.exceptions import ConflictError
from app.shared.security import Role

KOMENTAR = "Perbaiki rujukan pasal 151."

ISI = {
    "title": "PHK sepihak",
    "question": "Apakah PHK tanpa surat sah?",
    "legal_refs": [{"regulation_type": "UU", "regulation_number": "13", "pasal": "151"}],
    "answer_criteria": {"expected_conclusion": "tidak sah"},
}


def _user(db, nomor: int, role: Role, *, is_active: bool = True) -> uuid.UUID:
    user = User(
        id=uuid.UUID(f"00000000-0000-0000-0000-{nomor:012d}"),
        email=f"user{nomor}@veritask.test",
        name=f"User {nomor}",
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user.id


def _kasus(db, author_id: uuid.UUID, *, status: CaseStatus = CaseStatus.DRAFT) -> Case:
    suite_id = uuid.uuid4()
    case_id = uuid.uuid4()
    version_id = uuid.uuid4()
    db.add(
        Suite(
            id=suite_id,
            name=f"Suite {case_id}",
            status=SuiteStatus.ACTIVE,
            created_by=author_id,
        )
    )
    db.add(
        Case(
            id=case_id,
            suite_id=suite_id,
            case_code=f"PHK-{case_id.hex[:8]}",
            current_version_id=version_id,
            created_by=author_id,
            updated_by=author_id,
        )
    )
    db.add(
        CaseVersion(
            id=version_id,
            case_id=case_id,
            version_no=1,
            status=status,
            case_code=f"PHK-{case_id.hex[:8]}",
            split_tag=SplitTag.DEV,
            content=ISI,
            created_by=author_id,
        )
    )
    db.commit()
    return db.get(Case, case_id)


def _putaran(
    db, kasus, keputusan: list[tuple[uuid.UUID, VerdictDecision, str | None]]
) -> ReviewRound:
    ronde = ReviewRound(
        case_id=kasus.id,
        case_version_id=kasus.current_version_id,
        round_no=1,
        status=RoundStatus.DECIDED,
        decision=RoundDecision.REVISE,
    )
    db.add(ronde)
    db.flush()
    for reviewer_id, decision, komentar in keputusan:
        tugas = ReviewAssignment(
            review_round_id=ronde.id,
            reviewer_id=reviewer_id,
            source=AssignmentSource.SYSTEM,
            status=AssignmentStatus.ACTIVE,
        )
        db.add(tugas)
        db.flush()
        db.add(
            ReviewVerdict(
                assignment_id=tugas.id,
                decision=decision,
                comment=komentar,
            )
        )
    db.commit()
    return ronde


def _ajukan(db, kasus, penulis: uuid.UUID):
    request_context.bind(db, user_id=penulis, role=Role.CONTRIBUTOR)
    with (
        patch.object(
            cases_service.reviews_service,
            "assign_on_submit",
            wraps=reviews_service.assign_on_submit,
        ) as assign,
        patch.object(DbNotifier, "notify") as notify,
    ):
        hasil = cases_service.submit_for_review(db, kasus.id, actor_id=penulis)
    return hasil, assign, notify


def _tugas(db, ronde: ReviewRound) -> list[ReviewAssignment]:
    return db.query(ReviewAssignment).filter_by(review_round_id=ronde.id).all()


def _verdict(db, tugas: ReviewAssignment) -> ReviewVerdict | None:
    return db.query(ReviewVerdict).filter_by(assignment_id=tugas.id).one_or_none()


def test_pengajuan_ulang_membawa_approve_dan_hanya_menugaskan_perevisi(db_session):
    """AC9: approval tetap, hanya yang meminta revisi meninjau lagi."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    penyetuju = _user(db_session, 2, Role.CONTRIBUTOR)
    perevisi = _user(db_session, 3, Role.CONTRIBUTOR)
    ketiga = _user(db_session, 4, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    _putaran(
        db_session,
        kasus,
        [
            (penyetuju, VerdictDecision.APPROVE, None),
            (perevisi, VerdictDecision.REVISE, KOMENTAR),
        ],
    )
    lama = db_session.query(ReviewVerdict).filter_by(comment=KOMENTAR).one()

    # Act
    hasil, assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    assert hasil.round_no == 2
    assert hasil.round_status == RoundStatus.OPEN
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    assert baru.status == RoundStatus.OPEN
    tugas = _tugas(db_session, baru)
    tanpa_verdict = [baris for baris in tugas if _verdict(db_session, baris) is None]
    assert [baris.reviewer_id for baris in tanpa_verdict] == [perevisi]
    assert tanpa_verdict[0].source == AssignmentSource.SYSTEM
    assert tanpa_verdict[0].status == AssignmentStatus.ACTIVE
    dibawa = [verdict for baris in tugas if (verdict := _verdict(db_session, baris)) is not None]
    assert len(dibawa) == 1
    assert dibawa[0].decision == VerdictDecision.APPROVE
    assert dibawa[0].carried_from_verdict_id == (
        db_session.query(ReviewVerdict)
        .filter_by(decision=VerdictDecision.APPROVE, carried_from_verdict_id=None)
        .one()
        .id
    )
    assert {baris.reviewer_id for baris in tugas} == {penyetuju, perevisi}
    assert ketiga not in {baris.reviewer_id for baris in tugas}
    db_session.refresh(lama)
    assert lama.comment == KOMENTAR
    assert lama.decision == VerdictDecision.REVISE
    assert assign.call_count == 0
    assert notify.call_count == 1
    diberitahu = next(arg for arg in notify.call_args.args if isinstance(arg, list))
    assert diberitahu == [perevisi]
    assert notify.call_args.args[2]["link"] == f"/reviews/{tanpa_verdict[0].id}"
    pengajuan = [
        baris
        for baris in db_session.query(AuditLog).filter_by(action="case.submitted")
        if (baris.after or {}).get("round_no") == 2
    ]
    assert len(pengajuan) == 1
    assert pengajuan[0].actor_user_id == penulis
    assert pengajuan[0].actor_role == "contributor"
    assert pengajuan[0].case_id == kasus.id
    dibawa_audit = list(db_session.query(AuditLog).filter_by(action="review.approval_carried"))
    assert len(dibawa_audit) == 1
    assert dibawa_audit[0].actor_user_id == penulis
    assert dibawa_audit[0].case_id == kasus.id
    assert dibawa_audit[0].entity_id == next(
        baris.id for baris in tugas if baris.reviewer_id == penyetuju
    )
    assert dibawa_audit[0].after == {
        "decision": "approve",
        "carried_from_verdict_id": str(dibawa[0].carried_from_verdict_id),
    }
    assert (
        db_session.query(AuditLog)
        .filter_by(action="review.verdict_submitted", entity_id=dibawa_audit[0].entity_id)
        .count()
        == 0
    )


def test_dua_perevisi_keduanya_ditugaskan_lagi_tanpa_approval_dibawa(db_session):
    """Kalau keduanya meminta revisi, tidak ada yang dibawa dan keduanya meninjau lagi."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    pertama = _user(db_session, 2, Role.CONTRIBUTOR)
    kedua = _user(db_session, 3, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    _putaran(
        db_session,
        kasus,
        [
            (pertama, VerdictDecision.REVISE, "Jelaskan unsur pertama."),
            (kedua, VerdictDecision.REVISE, "Jelaskan unsur kedua."),
        ],
    )

    # Act
    _hasil, _assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    tugas = _tugas(db_session, baru)
    assert {baris.reviewer_id for baris in tugas} == {pertama, kedua}
    assert all(_verdict(db_session, baris) is None for baris in tugas)
    assert (
        db_session.query(ReviewVerdict)
        .filter(ReviewVerdict.carried_from_verdict_id.is_not(None))
        .count()
        == 0
    )
    assert notify.call_count == 2
    diberitahu = [panggilan.args[0] for panggilan in notify.call_args_list]
    assert sorted(diberitahu) == sorted([[pertama], [kedua]])
    assert db_session.query(ReviewVerdict).filter_by(comment="Jelaskan unsur pertama.").one()
    assert db_session.query(ReviewVerdict).filter_by(comment="Jelaskan unsur kedua.").one()


@pytest.mark.parametrize(
    ("status_round", "keputusan"),
    [
        (RoundStatus.AWAITING_ASSIGNMENT, None),
        (RoundStatus.OPEN, None),
        (RoundStatus.DECIDED, RoundDecision.APPROVED),
    ],
)
def test_pengajuan_ulang_ditolak_bila_bukan_keputusan_revisi(db_session, status_round, keputusan):
    """Round terakhir harus decided dengan keputusan revise."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    db_session.add(
        ReviewRound(
            case_id=kasus.id,
            case_version_id=kasus.current_version_id,
            round_no=1,
            status=status_round,
            decision=keputusan,
        )
    )
    db_session.commit()

    # Act
    with pytest.raises(ConflictError) as info:
        _ajukan(db_session, kasus, penulis)

    # Assert
    assert info.value.code == "ROUND_NOT_REVISED"
    assert db_session.query(ReviewRound).filter_by(round_no=2).count() == 0
    db_session.rollback()
    assert db_session.get(Case, kasus.id).current_version.status == CaseStatus.NEEDS_REVISION


def test_perevisi_nonaktif_tidak_diganti_dan_round_menunggu_penugasan(db_session):
    """SCRUM-147 yang mengganti reviewer. Di sini kursinya dibiarkan kosong."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    penyetuju = _user(db_session, 2, Role.CONTRIBUTOR)
    perevisi = _user(db_session, 3, Role.CONTRIBUTOR, is_active=False)
    pengganti = _user(db_session, 4, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    _putaran(
        db_session,
        kasus,
        [
            (penyetuju, VerdictDecision.APPROVE, None),
            (perevisi, VerdictDecision.REVISE, KOMENTAR),
        ],
    )

    # Act
    hasil, assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    assert hasil.round_status == RoundStatus.AWAITING_ASSIGNMENT
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    assert baru.status == RoundStatus.AWAITING_ASSIGNMENT
    tugas = _tugas(db_session, baru)
    assert {baris.reviewer_id for baris in tugas} == {penyetuju}
    assert all(_verdict(db_session, baris) is not None for baris in tugas)
    assert perevisi not in {baris.reviewer_id for baris in tugas}
    assert pengganti not in {baris.reviewer_id for baris in tugas}
    assert assign.call_count == 0
    assert notify.call_count == 0
    assert db_session.query(ReviewVerdict).filter_by(comment=KOMENTAR).one().comment == KOMENTAR


def test_perevisi_nonaktif_memberitahu_admin_aktif(db_session):
    """Round menunggu penugasan: hanya Admin aktif yang diberi tahu, beserta kode kasus."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    penyetuju = _user(db_session, 2, Role.CONTRIBUTOR)
    perevisi = _user(db_session, 3, Role.CONTRIBUTOR, is_active=False)
    pengganti = _user(db_session, 4, Role.CONTRIBUTOR)
    admin = _user(db_session, 10, Role.ADMIN)
    _user(db_session, 11, Role.ADMIN, is_active=False)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    _putaran(
        db_session,
        kasus,
        [
            (penyetuju, VerdictDecision.APPROVE, None),
            (perevisi, VerdictDecision.REVISE, KOMENTAR),
        ],
    )

    # Act
    hasil, assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    assert hasil.round_status == RoundStatus.AWAITING_ASSIGNMENT
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    tugas = _tugas(db_session, baru)
    assert {baris.reviewer_id for baris in tugas} == {penyetuju}
    assert perevisi not in {baris.reviewer_id for baris in tugas}
    assert pengganti not in {baris.reviewer_id for baris in tugas}
    assert assign.call_count == 0
    assert notify.call_count == 1
    user_ids, jenis, payload = notify.call_args.args
    assert user_ids == [admin]
    assert jenis == "review_needs_assignment"
    assert payload["title"] == f"{kasus.case_code} perlu penugasan reviewer"
    assert payload["link"] == "/admin/reviews"


def test_dua_perevisi_satu_nonaktif_round_menunggu_dengan_satu_kursi(db_session):
    """Kedua revise, satu nonaktif: round 2 kurang kursi, jadi awaiting_assignment."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    aktif = _user(db_session, 2, Role.CONTRIBUTOR)
    nonaktif = _user(db_session, 3, Role.CONTRIBUTOR, is_active=False)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    _putaran(
        db_session,
        kasus,
        [
            (aktif, VerdictDecision.REVISE, "Perbaiki unsur A."),
            (nonaktif, VerdictDecision.REVISE, "Perbaiki unsur B."),
        ],
    )

    # Act
    hasil, assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    assert hasil.round_status == RoundStatus.AWAITING_ASSIGNMENT
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    assert baru.status == RoundStatus.AWAITING_ASSIGNMENT
    tugas = _tugas(db_session, baru)
    assert len(tugas) == 1
    assert tugas[0].reviewer_id == aktif
    assert _verdict(db_session, tugas[0]) is None
    assert assign.call_count == 0
    assert notify.call_count == 1
    diberitahu = next(arg for arg in notify.call_args.args if isinstance(arg, list))
    assert diberitahu == [aktif]


def test_approve_void_plus_perevisi_round_menunggu_dengan_satu_kursi(db_session):
    """Approve void (seperti #50) + revise aktif: hanya 1 kursi di round 2."""
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    penyetuju = _user(db_session, 2, Role.CONTRIBUTOR)
    perevisi = _user(db_session, 3, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)
    lama = _putaran(
        db_session,
        kasus,
        [
            (penyetuju, VerdictDecision.APPROVE, None),
            (perevisi, VerdictDecision.REVISE, KOMENTAR),
        ],
    )
    tugas_penyetuju = (
        db_session.query(ReviewAssignment)
        .filter_by(review_round_id=lama.id, reviewer_id=penyetuju)
        .one()
    )
    tugas_penyetuju.status = AssignmentStatus.VOID
    db_session.commit()

    # Act
    hasil, assign, notify = _ajukan(db_session, kasus, penulis)

    # Assert
    assert hasil.round_status == RoundStatus.AWAITING_ASSIGNMENT
    baru = (
        db_session.query(ReviewRound)
        .filter_by(case_version_id=kasus.current_version_id, round_no=2)
        .one()
    )
    assert baru.status == RoundStatus.AWAITING_ASSIGNMENT
    tugas = _tugas(db_session, baru)
    assert len(tugas) == 1
    assert tugas[0].reviewer_id == perevisi
    assert _verdict(db_session, tugas[0]) is None
    assert (
        db_session.query(ReviewVerdict)
        .filter(ReviewVerdict.carried_from_verdict_id.is_not(None))
        .count()
        == 0
    )
    assert assign.call_count == 0
    assert notify.call_count == 1
    diberitahu = next(arg for arg in notify.call_args.args if isinstance(arg, list))
    assert diberitahu == [perevisi]


def test_pengajuan_ulang_ditolak_bila_belum_ada_round(db_session):
    # Arrange
    penulis = _user(db_session, 1, Role.CONTRIBUTOR)
    kasus = _kasus(db_session, penulis, status=CaseStatus.NEEDS_REVISION)

    # Act
    with pytest.raises(ConflictError) as info:
        _ajukan(db_session, kasus, penulis)

    # Assert
    assert info.value.code == "ROUND_NOT_REVISED"
    assert db_session.query(ReviewRound).count() == 0
    db_session.rollback()
    assert db_session.get(Case, kasus.id).current_version.status == CaseStatus.NEEDS_REVISION
