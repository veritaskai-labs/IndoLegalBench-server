"""Konfirmasi Admin sebelum ubah peran / nonaktifkan reviewer (PBI-24 AC8, SCRUM-197).

Yang dihitung hanya kasus yang masih menunggu penilaian reviewer itu:
assignment active tanpa verdict di round yang belum diputuskan. Tanpa
confirm, server membalas 409 CONFIRMATION_REQUIRED dan tidak mengubah apa pun.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User, UserSession
from app.modules.auth.schemas import UserUpdateRoleRequest
from app.modules.auth.seeds import ADMIN_SUB
from app.modules.auth.service import AuthService
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
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
from app.shared.exceptions import DomainError
from app.shared.security import CurrentUser, Role
from tests.login import complete_login

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000001a1")
AUTHOR_ID = uuid.UUID("00000000-0000-0000-0000-0000000001a2")
TARGET_ID = uuid.UUID("00000000-0000-0000-0000-0000000001a3")
OTHER_ID = uuid.UUID("00000000-0000-0000-0000-0000000001a4")

# Satu tempat untuk peran yang bisa mereview: setelah SCRUM-195 (#76) cukup
# diganti jadi Role.CONTRIBUTOR.
PERAN_REVIEWER = Role.REVIEWER


@pytest.fixture
def admin():
    return CurrentUser(ADMIN_ID, "Admin", "admin197@veritask.test", Role.ADMIN)


@pytest.fixture
def service(db_session):
    return AuthService(db_session)


@pytest.fixture
def anggota(db_session):
    db_session.add_all(
        [
            User(id=ADMIN_ID, email="admin197@veritask.test", name="Admin", role=Role.ADMIN),
            User(id=AUTHOR_ID, email="author197@veritask.test", name="Penulis", role=Role.AUTHOR),
            User(id=TARGET_ID, email="target197@veritask.test", name="Target", role=PERAN_REVIEWER),
            User(id=OTHER_ID, email="other197@veritask.test", name="Lain", role=PERAN_REVIEWER),
        ]
    )
    db_session.commit()


@pytest.fixture
def buat_tugas(db_session, anggota):
    """Satu kasus baru dengan satu round; mengembalikan assignment milik TARGET."""
    suite = Suite(name="Suite 197", status=SuiteStatus.ACTIVE, created_by=AUTHOR_ID)
    db_session.add(suite)
    db_session.flush()
    nomor = iter(range(1, 100))

    def _buat(
        *,
        round_status: RoundStatus = RoundStatus.OPEN,
        decision: RoundDecision | None = None,
        status: AssignmentStatus = AssignmentStatus.ACTIVE,
        dengan_verdict: bool = False,
    ) -> ReviewAssignment:
        kode = f"TK-197-{next(nomor):02d}"
        kasus = Case(
            case_code=kode,
            suite_id=suite.id,
            created_by=AUTHOR_ID,
            updated_by=AUTHOR_ID,
            current_version_id=uuid.uuid4(),
        )
        db_session.add(kasus)
        db_session.flush()
        versi = CaseVersion(
            id=kasus.current_version_id,
            case_id=kasus.id,
            case_code=kode,
            version_no=1,
            split_tag=SplitTag.DEV,
            status=CaseStatus.IN_REVIEW,
            content={"title": kode, "question": "?"},
            created_by=AUTHOR_ID,
        )
        db_session.add(versi)
        db_session.flush()
        ronde = ReviewRound(
            case_id=kasus.id,
            case_version_id=versi.id,
            round_no=1,
            status=round_status,
            decision=decision,
        )
        db_session.add(ronde)
        db_session.flush()
        tugas = ReviewAssignment(
            review_round_id=ronde.id,
            reviewer_id=TARGET_ID,
            source=AssignmentSource.SYSTEM,
            status=status,
        )
        tugas.review_round = ronde
        db_session.add(tugas)
        db_session.flush()
        if dengan_verdict:
            db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
        db_session.commit()
        return tugas

    return _buat


def _ubah_peran(service, admin, peran=Role.VIEWER):
    return service.update_member_role(TARGET_ID, UserUpdateRoleRequest(role=peran), admin)


def _target(db_session) -> User:
    db_session.expire_all()
    return db_session.get(User, TARGET_ID)


def _assert_minta_konfirmasi(exc: DomainError, jumlah: int) -> None:
    assert exc.status_code == 409
    assert exc.code == "CONFIRMATION_REQUIRED"
    assert exc.details == {"affected_count": jumlah}
    assert str(jumlah) in exc.message


# --- Tanpa confirm: 409, tidak ada yang berubah -------------------------------


def test_ubah_peran_dengan_tugas_tertunda_minta_konfirmasi(db_session, service, admin, buat_tugas):
    tugas_a = buat_tugas()
    tugas_b = buat_tugas()

    with pytest.raises(DomainError) as exc:
        _ubah_peran(service, admin)

    _assert_minta_konfirmasi(exc.value, 2)
    assert _target(db_session).role == PERAN_REVIEWER
    for tugas in (tugas_a, tugas_b):
        db_session.refresh(tugas)
        assert tugas.status == AssignmentStatus.ACTIVE
        assert tugas.review_round.status == RoundStatus.OPEN


def test_nonaktifkan_dengan_tugas_tertunda_minta_konfirmasi_dan_sesi_utuh(
    db_session, service, admin, buat_tugas
):
    tugas = buat_tugas()
    db_session.add(
        UserSession(user_id=TARGET_ID, expires_at=datetime.now(UTC) + timedelta(hours=1))
    )
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        service.deactivate_member(TARGET_ID, admin)

    _assert_minta_konfirmasi(exc.value, 1)
    assert _target(db_session).is_active is True
    assert db_session.query(UserSession).filter_by(user_id=TARGET_ID).count() == 1
    db_session.refresh(tugas)
    assert tugas.status == AssignmentStatus.ACTIVE


def test_tugas_yang_sudah_ada_verdict_tidak_dihitung(db_session, service, admin, buat_tugas):
    buat_tugas()
    buat_tugas(dengan_verdict=True)

    with pytest.raises(DomainError) as exc:
        service.deactivate_member(TARGET_ID, admin)

    _assert_minta_konfirmasi(exc.value, 1)


# --- Tidak ada yang menunggu penilaian: jalan tanpa konfirmasi ----------------


@pytest.mark.parametrize(
    "kondisi",
    [
        pytest.param({}, id="tanpa-tugas"),
        pytest.param({"dengan_verdict": True}, id="verdict-sudah-masuk"),
        pytest.param(
            {"round_status": RoundStatus.DECIDED, "decision": RoundDecision.APPROVED},
            id="round-sudah-diputuskan",
        ),
        pytest.param({"status": AssignmentStatus.REPLACED}, id="assignment-replaced"),
        pytest.param({"status": AssignmentStatus.VOID}, id="assignment-void"),
    ],
)
def test_tanpa_tugas_tertunda_ubah_peran_langsung_jalan(
    db_session, service, admin, buat_tugas, kondisi
):
    if kondisi:
        buat_tugas(**kondisi)

    hasil = _ubah_peran(service, admin)

    assert hasil.role == Role.VIEWER


def test_peran_sama_tidak_minta_konfirmasi(db_session, service, admin, buat_tugas):
    tugas = buat_tugas()

    hasil = _ubah_peran(service, admin, peran=PERAN_REVIEWER)

    assert hasil.role == PERAN_REVIEWER
    db_session.refresh(tugas)
    assert tugas.status == AssignmentStatus.ACTIVE


def test_akun_sendiri_tetap_ditolak_sebelum_hitung_tugas(service, anggota):
    diri = CurrentUser(TARGET_ID, "Target", "target197@veritask.test", Role.ADMIN)

    with pytest.raises(DomainError) as exc:
        service.deactivate_member(TARGET_ID, diri)

    assert exc.value.code == "CANNOT_DEACTIVATE_SELF"


# --- Lewat HTTP: bentuk body 409 dan parameter confirm --------------------------


@pytest.fixture
def login_admin(client, db_session):
    """Login dulu: seed_users dilewati bila tabel users sudah berisi."""
    complete_login(client, db_session, ADMIN_SUB)
    return client


def test_http_409_memakai_error_envelope(login_admin, buat_tugas):
    client = login_admin
    buat_tugas()

    ubah = client.patch(f"/admin/users/{TARGET_ID}", json={"role": "viewer"})
    nonaktif = client.post(f"/admin/users/{TARGET_ID}/deactivate")

    for respons in (ubah, nonaktif):
        assert respons.status_code == 409
        body = respons.json()
        assert body["code"] == "CONFIRMATION_REQUIRED"
        assert body["affected_count"] == 1
        assert body["message"]


def test_http_confirm_bukan_boolean_ditolak(login_admin, anggota):
    respons = login_admin.post(
        f"/admin/users/{TARGET_ID}/deactivate", params={"confirm": "mungkin"}
    )

    assert respons.status_code == 422
