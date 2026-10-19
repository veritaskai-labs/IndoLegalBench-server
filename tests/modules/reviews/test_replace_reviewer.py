"""Test replace reviewer dan antrean penugasan ulang (SCRUM-147, AC7, AC8).

PBI-6:
- AC7: Admin dapat mengganti reviewer. Penilaian tidak dihitung, tercatat di audit beserta alasannya.
  Reviewer baru tidak boleh penulis atau reviewer lain di round itu -> 422.
- AC8: Reviewer yang dinonaktifkan akunnya otomatis muncul pada daftar kasus yang memerlukan penugasan ulang (GET /reviews/needs-reassignment).
"""

import uuid

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews import service
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
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.security import Role

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
AUTHOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
REVIEWER_1 = uuid.UUID("00000000-0000-0000-0000-000000000003")
REVIEWER_2 = uuid.UUID("00000000-0000-0000-0000-000000000004")
REVIEWER_3 = uuid.UUID("00000000-0000-0000-0000-000000000005")
REVIEWER_INACTIVE = uuid.UUID("00000000-0000-0000-0000-000000000006")


class FakeNotifier:
    def __init__(self):
        self.panggilan = []

    def notify(self, user_ids, type, payload):
        self.panggilan.append((list(user_ids), type, dict(payload)))


@pytest.fixture
def users(db_session):
    u_admin = User(id=ADMIN_ID, email="admin@veritask.test", name="Admin", role=Role.ADMIN)
    u_author = User(id=AUTHOR_ID, email="author@veritask.test", name="Author", role=Role.AUTHOR)
    u_rev1 = User(id=REVIEWER_1, email="rev1@veritask.test", name="Reviewer 1", role=Role.REVIEWER)
    u_rev2 = User(id=REVIEWER_2, email="rev2@veritask.test", name="Reviewer 2", role=Role.REVIEWER)
    u_rev3 = User(id=REVIEWER_3, email="rev3@veritask.test", name="Reviewer 3", role=Role.REVIEWER)
    u_inact = User(
        id=REVIEWER_INACTIVE,
        email="inact@veritask.test",
        name="Reviewer Inactive",
        role=Role.REVIEWER,
        is_active=False,
    )
    db_session.add_all([u_admin, u_author, u_rev1, u_rev2, u_rev3, u_inact])
    db_session.commit()


@pytest.fixture
def case_data(db_session, users):
    suite = Suite(name="Suite Ketenagakerjaan", status=SuiteStatus.ACTIVE, created_by=AUTHOR_ID)
    db_session.add(suite)
    db_session.flush()

    kasus = Case(
        case_code="TK-001",
        suite_id=suite.id,
        created_by=AUTHOR_ID,
        updated_by=AUTHOR_ID,
        current_version_id=uuid.uuid4(),  # placeholder, diisi setelah version dibuat
    )
    db_session.add(kasus)
    db_session.flush()

    version = CaseVersion(
        id=kasus.current_version_id,
        case_id=kasus.id,
        case_code="TK-001",
        version_no=1,
        split_tag=SplitTag.DEV,
        status=CaseStatus.IN_REVIEW,
        content={"title": "Kasus Lembur", "question": "Berapa upah lembur?"},
        created_by=AUTHOR_ID,
    )
    db_session.add(version)
    db_session.flush()

    ronde = ReviewRound(
        case_id=kasus.id,
        case_version_id=version.id,
        round_no=1,
        status=RoundStatus.OPEN,
    )
    db_session.add(ronde)
    db_session.flush()

    tugas1 = ReviewAssignment(
        review_round_id=ronde.id,
        reviewer_id=REVIEWER_1,
        source=AssignmentSource.SYSTEM,
        status=AssignmentStatus.ACTIVE,
    )
    tugas2 = ReviewAssignment(
        review_round_id=ronde.id,
        reviewer_id=REVIEWER_2,
        source=AssignmentSource.SYSTEM,
        status=AssignmentStatus.ACTIVE,
    )
    tugas1.review_round = ronde
    tugas2.review_round = ronde
    db_session.add_all([tugas1, tugas2])
    db_session.commit()

    return {
        "suite": suite,
        "case": kasus,
        "version": version,
        "round": ronde,
        "tugas1": tugas1,
        "tugas2": tugas2,
    }


# ==============================================================================
# Service Unit Tests: replace_reviewer
# ==============================================================================


def test_replace_reviewer_service_berhasil(db_session, case_data):
    notifier = FakeNotifier()
    tugas1 = case_data["tugas1"]
    ronde = case_data["round"]

    old_tugas, new_tugas = service.replace_reviewer(
        db_session,
        assignment_id=tugas1.id,
        new_reviewer_id=REVIEWER_3,
        reason="Reviewer berhalangan hadir",
        admin_id=ADMIN_ID,
        notifier=notifier,
    )

    assert old_tugas.status == AssignmentStatus.REPLACED
    assert old_tugas.ended_at is not None
    assert old_tugas.end_reason == "Reviewer berhalangan hadir"

    assert new_tugas.status == AssignmentStatus.ACTIVE
    assert new_tugas.reviewer_id == REVIEWER_3
    assert new_tugas.source == AssignmentSource.ADMIN
    assert new_tugas.assigned_by == ADMIN_ID
    assert new_tugas.review_round_id == ronde.id

    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.OPEN

    # Notifikasi dikirim ke reviewer baru
    assert len(notifier.panggilan) == 1
    user_ids, jenis, payload = notifier.panggilan[0]
    assert user_ids == [REVIEWER_3]
    assert jenis == "review_assigned"
    assert payload["link"] == f"/reviews/{new_tugas.id}"


def test_replace_reviewer_tidak_memberi_tahu_bila_commit_gagal(db_session, case_data, monkeypatch):
    """Notifikasi baru dikirim setelah commit, jadi tidak ada link ke assignment yang batal."""
    notifier = FakeNotifier()

    def commit_gagal():
        raise RuntimeError("commit gagal")

    monkeypatch.setattr(db_session, "commit", commit_gagal)

    with pytest.raises(RuntimeError):
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_3,
            reason="Reviewer berhalangan hadir",
            admin_id=ADMIN_ID,
            notifier=notifier,
        )

    assert notifier.panggilan == []


def test_replace_reviewer_tanpa_notifier_memberi_tahu_reviewer_baru_lewat_db_notifier(
    db_session, case_data, monkeypatch
):
    """Router tidak menyuntikkan notifier, jadi default service harus tetap mengirim."""
    notifier = FakeNotifier()
    monkeypatch.setattr(service, "DbNotifier", lambda: notifier)

    _, new_tugas = service.replace_reviewer(
        db_session,
        assignment_id=case_data["tugas1"].id,
        new_reviewer_id=REVIEWER_3,
        reason="Reviewer berhalangan hadir",
        admin_id=ADMIN_ID,
    )

    assert notifier.panggilan == [
        (
            [REVIEWER_3],
            "review_assigned",
            {
                "title": "Tugas review baru",
                "body": "Anda ditugaskan mereview sebuah kasus.",
                "link": f"/reviews/{new_tugas.id}",
            },
        )
    ]


def test_replace_reviewer_void_tanpa_notifier_memberi_tahu_reviewer_baru(
    db_session, case_data, monkeypatch
):
    """AC8: penggantian reviewer nonaktif (assignment void) juga memberi tahu pengganti."""
    notifier = FakeNotifier()
    monkeypatch.setattr(service, "DbNotifier", lambda: notifier)
    tugas1 = case_data["tugas1"]
    tugas1.status = AssignmentStatus.VOID
    tugas1.end_reason = "Reviewer deactivated"
    case_data["round"].status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()

    _, new_tugas = service.replace_reviewer(
        db_session,
        assignment_id=tugas1.id,
        new_reviewer_id=REVIEWER_3,
        reason="Penugasan ulang pengganti akun dinonaktifkan",
        admin_id=ADMIN_ID,
    )

    assert len(notifier.panggilan) == 1
    user_ids, jenis, payload = notifier.panggilan[0]
    assert user_ids == [REVIEWER_3]
    assert jenis == "review_assigned"
    assert payload["link"] == f"/reviews/{new_tugas.id}"


def test_replace_reviewer_ditolak_tidak_memberi_tahu_siapa_pun(db_session, case_data, monkeypatch):
    """Penggantian yang gagal validasi tidak boleh meninggalkan notifikasi."""
    notifier = FakeNotifier()
    monkeypatch.setattr(service, "DbNotifier", lambda: notifier)

    with pytest.raises(ValidationError):
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_2,
            reason="Reviewer sudah ada di round ini",
            admin_id=ADMIN_ID,
        )

    assert notifier.panggilan == []


def test_replace_reviewer_service_mengganti_assignment_void(db_session, case_data):
    """AC8: Mengganti reviewer yang sebelumnya di-void akunnya."""
    tugas1 = case_data["tugas1"]
    ronde = case_data["round"]
    tugas1.status = AssignmentStatus.VOID
    tugas1.end_reason = "Reviewer deactivated"
    ronde.status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()

    old_tugas, new_tugas = service.replace_reviewer(
        db_session,
        assignment_id=tugas1.id,
        new_reviewer_id=REVIEWER_3,
        reason="Penugasan ulang pengganti akun dinonaktifkan",
        admin_id=ADMIN_ID,
    )

    assert old_tugas.status == AssignmentStatus.REPLACED
    assert new_tugas.status == AssignmentStatus.ACTIVE
    assert new_tugas.reviewer_id == REVIEWER_3

    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.OPEN


def test_replace_reviewer_penilaian_lama_tidak_dihitung(db_session, case_data):
    """AC7: Penilaian yang sudah diberikan reviewer sebelumnya tidak dihitung."""
    tugas1 = case_data["tugas1"]
    verdict = ReviewVerdict(assignment_id=tugas1.id, decision=VerdictDecision.APPROVE)
    db_session.add(verdict)
    db_session.commit()

    old_tugas, new_tugas = service.replace_reviewer(
        db_session,
        assignment_id=tugas1.id,
        new_reviewer_id=REVIEWER_3,
        reason="Ganti reviewer",
        admin_id=ADMIN_ID,
    )

    assert old_tugas.status == AssignmentStatus.REPLACED
    # Verdict tetap terpasang pada old assignment tapi statusnya REPLACED
    aktif_assignments = service.repository.get_active_assignments_for_round(
        db_session, case_data["round"].id
    )
    assert old_tugas.id not in [a.id for a in aktif_assignments]


def test_replace_reviewer_service_validasi_alasan_kosong(db_session, case_data):
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_3,
            reason="   ",
            admin_id=ADMIN_ID,
        )
    assert exc.value.field == "reason"
    assert exc.value.code == "REASON_REQUIRED"


def test_replace_reviewer_service_assignment_tidak_ditemukan(db_session, users):
    with pytest.raises(NotFoundError):
        service.replace_reviewer(
            db_session,
            assignment_id=uuid.uuid4(),
            new_reviewer_id=REVIEWER_3,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )


def test_replace_reviewer_service_assignment_sudah_diganti(db_session, case_data):
    tugas1 = case_data["tugas1"]
    tugas1.status = AssignmentStatus.REPLACED
    tugas1.end_reason = "Alasan sebelumnya"
    db_session.commit()

    with pytest.raises(ConflictError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=tugas1.id,
            new_reviewer_id=REVIEWER_3,
            reason="Ganti lagi",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "ASSIGNMENT_ALREADY_REPLACED"


def test_replace_reviewer_service_round_sudah_diputuskan(db_session, case_data):
    ronde = case_data["round"]
    ronde.status = RoundStatus.DECIDED
    ronde.decision = RoundDecision.APPROVED
    db_session.commit()

    with pytest.raises(ConflictError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_3,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "ROUND_ALREADY_DECIDED"


def test_replace_reviewer_service_reviewer_baru_tidak_ditemukan(db_session, case_data):
    with pytest.raises(NotFoundError):
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=uuid.uuid4(),
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )


def test_replace_reviewer_service_reviewer_baru_tidak_aktif(db_session, case_data):
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_INACTIVE,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "INVALID_REVIEWER"


def test_replace_reviewer_service_reviewer_baru_bukan_peran_reviewer(db_session, case_data):
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=ADMIN_ID,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "INVALID_REVIEWER"


def test_replace_reviewer_service_reviewer_baru_adalah_penulis(db_session, case_data):
    """AC7: Reviewer baru tidak boleh penulis kasus -> 422."""
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=AUTHOR_ID,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "CANNOT_ASSIGN_AUTHOR"


def test_replace_reviewer_service_reviewer_baru_adalah_reviewer_lain_di_round(
    db_session, case_data
):
    """AC7: Reviewer baru tidak boleh reviewer lain di round itu -> 422."""
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_2,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "REVIEWER_ALREADY_ASSIGNED"


def test_replace_reviewer_service_reviewer_baru_sama_dengan_yang_diganti(db_session, case_data):
    with pytest.raises(ValidationError) as exc:
        service.replace_reviewer(
            db_session,
            assignment_id=case_data["tugas1"].id,
            new_reviewer_id=REVIEWER_1,
            reason="Ganti reviewer",
            admin_id=ADMIN_ID,
        )
    assert exc.value.code == "REVIEWER_ALREADY_ASSIGNED"


# ==============================================================================
# Service Unit Tests: void_reviewer_assignments & get_needs_reassignment
# ==============================================================================


def test_void_reviewer_assignments_service(db_session, case_data):
    """AC8: Penonaktifan reviewer me-void assignment aktif dan mengubah status round."""
    ronde = case_data["round"]
    tugas1 = case_data["tugas1"]

    voided = service.void_reviewer_assignments(db_session, REVIEWER_1)

    assert len(voided) == 1
    assert voided[0].id == tugas1.id
    assert voided[0].status == AssignmentStatus.VOID
    assert voided[0].ended_at is not None
    assert voided[0].end_reason == "Reviewer deactivated"

    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.AWAITING_ASSIGNMENT


def test_void_reviewer_assignments_abaikan_round_sudah_decided(db_session, case_data):
    ronde = case_data["round"]
    ronde.status = RoundStatus.DECIDED
    ronde.decision = RoundDecision.APPROVED
    db_session.commit()

    voided = service.void_reviewer_assignments(db_session, REVIEWER_1)

    assert len(voided) == 0
    db_session.refresh(ronde)
    assert ronde.status == RoundStatus.DECIDED
    db_session.refresh(case_data["tugas1"])
    assert case_data["tugas1"].status == AssignmentStatus.ACTIVE


def test_get_needs_reassignment_service(db_session, case_data):
    """AC8: Antrean penugasan ulang memuat kasus yang perlu penugasan."""
    tugas1 = case_data["tugas1"]
    tugas1.status = AssignmentStatus.VOID
    tugas1.end_reason = "Reviewer deactivated"
    case_data["round"].status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()

    antrean = service.get_needs_reassignment(db_session)

    assert len(antrean) == 1
    item = antrean[0]
    assert item.case_id == case_data["case"].id
    assert item.case_code == "TK-001"
    assert item.case_title == "Kasus Lembur"
    assert item.round_id == case_data["round"].id
    assert item.round_status == RoundStatus.AWAITING_ASSIGNMENT
    assert item.active_reviewers_count == 1
    assert len(item.void_assignments) == 1
    assert item.void_assignments[0].id == tugas1.id
    assert len(item.assignments) == 2


def test_get_needs_reassignment_kosong_bila_semua_lengkap(db_session, case_data):
    antrean = service.get_needs_reassignment(db_session)
    assert antrean == []


# ==============================================================================
# HTTP API Tests: POST /reviews/{assignment_id}/replace & GET /reviews/needs-reassignment
# ==============================================================================


def test_api_replace_reviewer_oleh_admin(as_role, case_data):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    tugas1 = case_data["tugas1"]

    resp = client.post(
        f"/reviews/{tugas1.id}/replace",
        json={"new_reviewer_id": str(REVIEWER_3), "reason": "Admin mereplace reviewer"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["replaced_assignment_id"] == str(tugas1.id)
    assert data["reviewer_id"] == str(REVIEWER_3)
    assert data["status"] == "active"
    assert data["source"] == "admin"


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_api_replace_reviewer_peran_non_admin_dilarang(as_role, case_data, role):
    client = as_role(role, user_id=AUTHOR_ID)
    tugas1 = case_data["tugas1"]

    resp = client.post(
        f"/reviews/{tugas1.id}/replace",
        json={"new_reviewer_id": str(REVIEWER_3), "reason": "Ganti reviewer"},
    )
    assert resp.status_code == 403


def test_api_replace_reviewer_validasi_422_penulis(as_role, case_data):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    tugas1 = case_data["tugas1"]

    resp = client.post(
        f"/reviews/{tugas1.id}/replace",
        json={"new_reviewer_id": str(AUTHOR_ID), "reason": "Ganti reviewer"},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "CANNOT_ASSIGN_AUTHOR"


def test_api_replace_reviewer_validasi_422_reviewer_lain(as_role, case_data):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    tugas1 = case_data["tugas1"]

    resp = client.post(
        f"/reviews/{tugas1.id}/replace",
        json={"new_reviewer_id": str(REVIEWER_2), "reason": "Ganti reviewer"},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "REVIEWER_ALREADY_ASSIGNED"


def test_api_replace_reviewer_validasi_422_alasan_kosong(as_role, case_data):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    tugas1 = case_data["tugas1"]

    resp = client.post(
        f"/reviews/{tugas1.id}/replace",
        json={"new_reviewer_id": str(REVIEWER_3), "reason": "   "},
    )
    assert resp.status_code == 422


def test_api_replace_reviewer_404_assignment(as_role, users):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)

    resp = client.post(
        f"/reviews/{uuid.uuid4()}/replace",
        json={"new_reviewer_id": str(REVIEWER_3), "reason": "Ganti reviewer"},
    )
    assert resp.status_code == 404


def test_api_needs_reassignment_oleh_admin(as_role, case_data, db_session):
    client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    case_data["tugas1"].status = AssignmentStatus.VOID
    case_data["round"].status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()

    resp = client.get("/reviews/needs-reassignment")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["case_id"] == str(case_data["case"].id)
    assert data[0]["case_code"] == "TK-001"
    assert data[0]["round_status"] == "awaiting_assignment"


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_api_needs_reassignment_peran_non_admin_dilarang(as_role, role):
    client = as_role(role, user_id=AUTHOR_ID)
    resp = client.get("/reviews/needs-reassignment")
    assert resp.status_code == 403


# ==============================================================================
# End-to-End Integration Test (PBI-1 deactivation -> AC8 -> AC7 -> Audit)
# ==============================================================================


def test_e2e_deactivate_reviewer_muncul_di_antrean_lalu_diganti_dan_tercatat_audit(
    as_role, case_data, db_session
):
    """Skenario lengkap AC7 & AC8:

    1. Admin menonaktifkan Reviewer 1 lewat endpoint PBI-1.
    2. Assignment Reviewer 1 otomatis jadi void.
    3. Kasusnya muncul di GET /reviews/needs-reassignment.
    4. Admin mengganti reviewer lewat POST /reviews/{assignment_id}/replace dengan Reviewer 3.
    5. Kasus keluar dari antrean needs-reassignment.
    6. Audit log mencatat:
       - user.deactivated
       - review.assignment_voided
       - review.reviewer_replaced (beserta alasan)
       - review.reviewer_assigned
    """
    admin_client = as_role(Role.ADMIN, user_id=ADMIN_ID)
    tugas1 = case_data["tugas1"]
    kasus = case_data["case"]

    # 1. Admin menonaktifkan Reviewer 1 (endpoint PBI-1)
    deact_resp = admin_client.post(f"/admin/users/{REVIEWER_1}/deactivate")
    assert deact_resp.status_code == 200

    # 2. Assignment otomatis void di DB
    db_session.refresh(tugas1)
    assert tugas1.status == AssignmentStatus.VOID

    # 3. Kasus muncul di GET /reviews/needs-reassignment
    queue_resp = admin_client.get("/reviews/needs-reassignment")
    assert queue_resp.status_code == 200
    queue_data = queue_resp.json()
    assert len(queue_data) == 1
    assert queue_data[0]["case_id"] == str(kasus.id)
    assert queue_data[0]["void_assignments"][0]["id"] == str(tugas1.id)

    # 4. Admin mengganti reviewer via POST /reviews/{assignment_id}/replace
    replace_resp = admin_client.post(
        f"/reviews/{tugas1.id}/replace",
        json={
            "new_reviewer_id": str(REVIEWER_3),
            "reason": "Mengganti reviewer yang akunnya dinonaktifkan",
        },
    )
    assert replace_resp.status_code == 200

    # 5. Kasus keluar dari antrean
    queue_resp2 = admin_client.get("/reviews/needs-reassignment")
    assert queue_resp2.status_code == 200
    assert len(queue_resp2.json()) == 0

    # 6. Audit logs tercatat lengkap
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.case_id == kasus.id)
        .order_by(AuditLog.id.asc())
        .all()
    )
    actions = [log.action for log in logs]
    assert "review.assignment_voided" in actions
    assert "review.reviewer_replaced" in actions
    assert "review.reviewer_assigned" in actions

    # Cek alasan di event review.reviewer_replaced
    replace_log = next(log for log in logs if log.action == "review.reviewer_replaced")
    assert replace_log.reason == "Mengganti reviewer yang akunnya dinonaktifkan"
    assert replace_log.entity_id == tugas1.id
    assert replace_log.after == {"status": "replaced"}

    # Cek event voided
    void_log = next(log for log in logs if log.action == "review.assignment_voided")
    assert void_log.entity_id == tugas1.id
    assert void_log.after == {"status": "void"}

    # Cek event assigned baru
    assigned_log = next(
        log
        for log in logs
        if log.action == "review.reviewer_assigned" and log.after.get("source") == "admin"
    )
    assert assigned_log.after == {"reviewer_id": str(REVIEWER_3), "source": "admin"}
