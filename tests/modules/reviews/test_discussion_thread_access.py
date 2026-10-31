"""SCRUM-242: thread komentar terbuka untuk semua pihak, read-only setelah approved."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.reviews import discussion
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    RoundDecision,
    RoundStatus,
)
from app.modules.reviews.schemas import CommentReplyWrite
from app.modules.reviews.visibility import Viewer
from app.shared.exceptions import ConflictError
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.reviews.test_review_api import (
    REVIEWER_A,
    REVIEWER_B,
    _ajukan_ulang_setelah_revisi_b,
    _nilai,
    _tugas_milik,
)
from tests.modules.reviews.test_review_api import (
    jadi as jadi,
)
from tests.modules.reviews.test_review_api import (
    kasus as kasus,
)

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000242ad")


@pytest.fixture(autouse=True)
def isolate_existing_notifications(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.DbNotifier.notify", lambda *args: None)


def url(assignment):
    return f"/reviews/{assignment}/discussion"


def post(client, assignment, body="Terima kasih atas catatannya."):
    return client.post(f"/reviews/{assignment}/replies", json={"body": body})


@pytest.fixture
def revisi(kasus, jadi):
    """Round decided dengan A meminta revisi (ada komentar) dan B setuju."""
    client, case_id, ids = kasus
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    assert _nilai(client, ids[REVIEWER_A], "revise", "Mohon perjelas rujukan.").status_code == 201
    jadi(Role.CONTRIBUTOR, REVIEWER_B)
    assert _nilai(client, ids[REVIEWER_B], "approve").status_code == 201
    return client, case_id, ids


@pytest.fixture
def disetujui(kasus, jadi):
    """Round decided approved: kedua reviewer setuju, A menulis komentar."""
    client, case_id, ids = kasus
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    assert _nilai(client, ids[REVIEWER_A], "approve", "Catatan kecil saja.").status_code == 201
    jadi(Role.CONTRIBUTOR, REVIEWER_B)
    assert _nilai(client, ids[REVIEWER_B], "approve").status_code == 201
    return client, case_id, ids


@pytest.fixture
def disetujui_di_ronde_dua(kasus, jadi):
    """Round 1: B minta revisi (thread B). Round 2: B setuju, kasus approved."""
    client, case_id, ids = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, ids)
    tugas_b = _tugas_milik(client, jadi, REVIEWER_B)
    assert _nilai(client, tugas_b["assignment_id"]).json()["round_decision"] == "approved"
    return client, case_id, ids


# --- Baca oleh reviewer lain (AC3 blind rule tetap) -----------------------------


def test_reviewer_lain_membaca_thread_setelah_decided_tanpa_hak_balas(revisi, jadi):
    # Arrange
    client, _, ids = revisi
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    response = client.get(url(ids[REVIEWER_A]))

    # Assert
    assert response.status_code == 200
    assert response.json()["comment"] == "Mohon perjelas rujukan."
    assert response.json()["can_reply"] is False


def test_reviewer_lain_ditolak_membalas_thread_rekan(revisi, jadi):
    # Arrange
    client, _, ids = revisi
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    response = post(client, ids[REVIEWER_A])

    # Assert
    assert response.status_code == 403


def test_reviewer_lain_ditolak_membaca_sebelum_decided(kasus, jadi):
    # Arrange
    client, _, ids = kasus
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    _nilai(client, ids[REVIEWER_A], "revise", "Masih rahasia.")
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    response = client.get(url(ids[REVIEWER_A]))

    # Assert
    assert response.status_code == 403


def test_reviewer_yang_diganti_tidak_lagi_membaca_thread_rekan(revisi, jadi, db_session):
    # Arrange
    client, _, ids = revisi
    db_session.get(ReviewAssignment, uuid.UUID(ids[REVIEWER_B])).status = AssignmentStatus.REPLACED
    db_session.commit()
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    response = client.get(url(ids[REVIEWER_A]))

    # Assert
    assert response.status_code == 403


def test_admin_membaca_tetapi_ditolak_membalas(revisi, jadi):
    # Arrange
    client, _, ids = revisi
    jadi(Role.ADMIN, ADMIN_ID)

    # Act
    baca = client.get(url(ids[REVIEWER_A]))
    balas = post(client, ids[REVIEWER_A])

    # Assert
    assert baca.status_code == 200
    assert baca.json()["can_reply"] is False
    assert balas.status_code == 403


# --- Read-only setelah approved (409) --------------------------------------------


def test_balasan_setelah_approved_ditolak_409_untuk_penulis(disetujui, jadi):
    # Arrange
    client, _, ids = disetujui
    jadi(Role.CONTRIBUTOR, USER_ID_QA)

    # Act
    response = post(client, ids[REVIEWER_A])

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "THREAD_READ_ONLY"


def test_balasan_setelah_approved_ditolak_409_untuk_pemilik_komentar(disetujui, jadi):
    # Arrange
    client, _, ids = disetujui
    jadi(Role.CONTRIBUTOR, REVIEWER_A)

    # Act
    response = post(client, ids[REVIEWER_A])

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "THREAD_READ_ONLY"


@pytest.mark.parametrize(
    ("role", "user_id"),
    [
        (Role.CONTRIBUTOR, USER_ID_QA),
        (Role.CONTRIBUTOR, REVIEWER_A),
        (Role.CONTRIBUTOR, REVIEWER_B),
        (Role.ADMIN, ADMIN_ID),
    ],
)
def test_thread_approved_tetap_terbaca_semua_pihak_dengan_can_reply_false(
    disetujui, jadi, role, user_id
):
    # Arrange
    client, _, ids = disetujui
    jadi(role, user_id)

    # Act
    response = client.get(url(ids[REVIEWER_A]))

    # Assert
    assert response.status_code == 200
    assert response.json()["comment"] == "Catatan kecil saja."
    assert response.json()["can_reply"] is False


def test_thread_revisi_tetap_bisa_dibalas_penulis(revisi, jadi):
    """Regresi: round decided dengan hasil revise masih membuka diskusi."""
    # Arrange
    client, _, ids = revisi
    jadi(Role.CONTRIBUTOR, USER_ID_QA)

    # Act
    response = post(client, ids[REVIEWER_A])

    # Assert
    assert response.status_code == 201


# --- Kunci per versi kasus: thread ronde lama ikut terkunci (review PR #86) --------


def test_thread_ronde_lama_terkunci_setelah_ronde_berikutnya_approved(disetujui_di_ronde_dua, jadi):
    # Arrange
    client, _, ids = disetujui_di_ronde_dua
    jadi(Role.CONTRIBUTOR, USER_ID_QA)

    # Act
    baca = client.get(url(ids[REVIEWER_B]))
    balas = post(client, ids[REVIEWER_B])

    # Assert
    assert baca.status_code == 200
    assert baca.json()["can_reply"] is False
    assert balas.status_code == 409
    assert balas.json()["code"] == "THREAD_READ_ONLY"


def test_pemilik_komentar_ronde_lama_juga_ditolak_membalas_setelah_approved(
    disetujui_di_ronde_dua, jadi
):
    # Arrange
    client, _, ids = disetujui_di_ronde_dua
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    response = post(client, ids[REVIEWER_B])

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "THREAD_READ_ONLY"


def test_thread_ronde_revisi_masih_bisa_dibalas_selama_versi_belum_approved(kasus, jadi):
    """Regresi: ronde 1 revise, ronde 2 belum diputuskan, versi belum approved."""
    # Arrange
    client, case_id, ids = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, ids)
    jadi(Role.CONTRIBUTOR, USER_ID_QA)

    # Act
    response = post(client, ids[REVIEWER_B])

    # Assert
    assert response.status_code == 201


# --- Unit: repository dan dependency di-mock -------------------------------------


def _tugas_palsu(*, decision, reviewer_id=REVIEWER_A):
    ronde = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        round_no=1,
        status=RoundStatus.DECIDED,
        decision=decision,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        reviewer_id=reviewer_id,
        status=AssignmentStatus.ACTIVE,
        review_round=ronde,
        review_round_id=ronde.id,
        verdict=SimpleNamespace(
            comment="Perjelas pasal.", submitted_at=datetime(2026, 10, 1, tzinfo=UTC)
        ),
    )


@patch("app.modules.reviews.discussion.notifications")
@patch("app.modules.reviews.discussion.auth_service")
@patch("app.modules.reviews.discussion.repository")
@patch("app.modules.cases.service.case_author")
def test_reply_service_menolak_round_approved_tanpa_menulis(
    case_author, repo, _auth, notifications
):
    # Arrange
    task = _tugas_palsu(decision=RoundDecision.APPROVED)
    case_author.return_value = USER_ID_QA
    repo.get_assignment.return_value = task
    repo.get_active_assignments_for_round.return_value = [task]
    repo.version_has_approved_round.return_value = True
    db = MagicMock()

    # Act
    with pytest.raises(ConflictError) as info:
        discussion.reply(
            db, task.id, Viewer(USER_ID_QA, Role.CONTRIBUTOR), CommentReplyWrite(body="Halo")
        )

    # Assert
    assert info.value.code == "THREAD_READ_ONLY"
    db.add.assert_not_called()
    notifications.enqueue_review_reply.assert_not_called()


@patch("app.modules.reviews.discussion.auth_service")
@patch("app.modules.reviews.discussion.repository")
@patch("app.modules.cases.service.case_author")
def test_read_service_memuat_reviewer_lain_dengan_can_reply_false(case_author, repo, auth):
    # Arrange
    pemilik = _tugas_palsu(decision=RoundDecision.REVISE)
    rekan = SimpleNamespace(reviewer_id=REVIEWER_B, status=AssignmentStatus.ACTIVE)
    case_author.return_value = USER_ID_QA
    repo.get_assignment.return_value = pemilik
    repo.get_active_assignments_for_round.return_value = [pemilik, rekan]
    repo.comment_replies.return_value = []
    repo.version_has_approved_round.return_value = False
    auth.user_names.return_value = {REVIEWER_A: "Reviewer A"}

    # Act
    result = discussion.read(MagicMock(), pemilik.id, Viewer(REVIEWER_B, Role.CONTRIBUTOR))

    # Assert
    assert result.comment == "Perjelas pasal."
    assert result.reviewer_name == "Reviewer A"
    assert result.can_reply is False
    [(_db, round_id)] = [c.args for c in repo.get_active_assignments_for_round.call_args_list]
    assert round_id == pemilik.review_round_id


@patch("app.modules.reviews.discussion._reply_view")
@patch("app.modules.reviews.discussion.notifications")
@patch("app.modules.reviews.discussion.auth_service")
@patch("app.modules.reviews.discussion.repository")
@patch("app.modules.cases.service.case_author")
def test_reply_service_menolak_round_revise_bila_versi_sudah_approved(
    case_author, repo, _auth, notifications, _reply_view
):
    # Arrange
    task = _tugas_palsu(decision=RoundDecision.REVISE)
    case_author.return_value = USER_ID_QA
    repo.get_assignment.return_value = task
    repo.get_active_assignments_for_round.return_value = [task]
    repo.version_has_approved_round.return_value = True
    db = MagicMock()

    # Act
    with pytest.raises(ConflictError) as info:
        discussion.reply(
            db, task.id, Viewer(USER_ID_QA, Role.CONTRIBUTOR), CommentReplyWrite(body="Halo")
        )

    # Assert
    assert info.value.code == "THREAD_READ_ONLY"
    repo.version_has_approved_round.assert_called_once_with(db, task.review_round.case_version_id)
    db.add.assert_not_called()
    notifications.enqueue_review_reply.assert_not_called()


@patch("app.modules.reviews.discussion.auth_service")
@patch("app.modules.reviews.discussion.repository")
@patch("app.modules.cases.service.case_author")
def test_read_service_can_reply_false_bila_versi_sudah_approved(case_author, repo, auth):
    # Arrange
    task = _tugas_palsu(decision=RoundDecision.REVISE)
    case_author.return_value = USER_ID_QA
    repo.get_assignment.return_value = task
    repo.get_active_assignments_for_round.return_value = [task]
    repo.comment_replies.return_value = []
    repo.version_has_approved_round.return_value = True
    auth.user_names.return_value = {}

    # Act
    result = discussion.read(MagicMock(), task.id, Viewer(USER_ID_QA, Role.CONTRIBUTOR))

    # Assert
    assert result.can_reply is False
