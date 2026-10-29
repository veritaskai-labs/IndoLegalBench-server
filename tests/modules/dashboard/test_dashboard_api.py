"""Ringkasan dashboard per peran lewat HTTP. PBI-19 AC1, AC2, AC6, SCRUM-154.

"Menunggu review" mengikuti keputusan Kak Aileen (17 Okt): kasus in_review
yang round-nya belum punya verdict sah dari kedua slot reviewer. Verdict
reviewer yang sudah diganti tidak dihitung (PBI-6 AC7). Kasus dengan 0 atau
1 reviewer (awaiting_assignment) dan kasus yang reviewer-nya dinonaktifkan
dihitung di sini dan juga di angka perlu penugasan ulang milik Admin.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Depends

from app.main import app
from app.modules.auth.models import User
from app.modules.cases.models import CaseStatus
from app.modules.dashboard import service as dashboard_service
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)
from app.shared import request_context
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import _buat, _suite
from tests.modules.conftest import USER_ID_QA

REVIEWER_A = uuid.UUID("00000000-0000-0000-0000-0000000154a1")
REVIEWER_B = uuid.UUID("00000000-0000-0000-0000-0000000154a2")
REVIEWER_C = uuid.UUID("00000000-0000-0000-0000-0000000154a3")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000154ad")
KONTRIBUTOR_B = uuid.UUID("00000000-0000-0000-0000-0000000195b0")


@pytest.fixture
def jadi(buat_pengguna):
    """Ganti pengguna yang login dan ikat pelakunya ke session, seperti get_current_user."""

    def ganti(role: Role, user_id: uuid.UUID) -> None:
        pengguna = buat_pengguna(role, user_id=user_id)

        def pengguna_palsu(db=Depends(get_db)):
            request_context.bind(db, user_id=pengguna.user_id, role=pengguna.role, request_id=None)
            return pengguna

        app.dependency_overrides[get_current_user] = pengguna_palsu

    return ganti


@pytest.fixture
def author(as_role):
    """Client Author USER_ID_QA dan satu suite miliknya."""
    client = as_role(Role.CONTRIBUTOR)
    return client, _suite(client)


def _reviewer(db_session, *ids: uuid.UUID) -> None:
    db_session.add_all(
        [
            User(
                id=i,
                email=f"{str(i)[-3:]}@veritask.test",
                name=f"R{str(i)[-3:]}",
                role=Role.CONTRIBUTOR,
            )
            for i in ids
        ]
    )
    db_session.commit()


def _ajukan(client, suite_id: str, code: str) -> str:
    case_id = _buat(client, suite_id, case_code=code).json()["id"]
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    return case_id


def _round(db_session, case_id: str) -> ReviewRound:
    db_session.expire_all()
    return (
        db_session.query(ReviewRound)
        .filter_by(case_id=uuid.UUID(case_id))
        .order_by(ReviewRound.round_no.desc())
        .first()
    )


def _tugas(db_session, case_id: str) -> dict[uuid.UUID, ReviewAssignment]:
    ronde = _round(db_session, case_id)
    return {
        t.reviewer_id: t
        for t in db_session.query(ReviewAssignment).filter_by(review_round_id=ronde.id).all()
    }


def _nilai(client, assignment_id, decision: str = "approve", comment: str | None = None):
    return client.post(
        f"/reviews/{assignment_id}/verdict", json={"decision": decision, "comment": comment}
    )


def _dashboard(client) -> dict:
    response = client.get("/dashboard")
    assert response.status_code == 200, response.text
    return response.json()


# --- Akses ----------------------------------------------------------------------------


def test_viewer_tidak_punya_dashboard(as_role):
    # Arrange
    client = as_role(Role.VIEWER)

    # Act
    response = client.get("/dashboard")

    # Assert
    assert response.status_code == 403


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.CONTRIBUTOR, Role.ADMIN])
def test_author_reviewer_admin_mendapat_dashboard(as_role, role):
    # Act
    response = as_role(role).get("/dashboard")

    # Assert
    assert response.status_code == 200


# --- AC1: jumlah per status dan menunggu review ---------------------------------------


def test_dashboard_kosong_semua_nol(author):
    # Arrange
    client, _ = author

    # Act
    body = _dashboard(client)

    # Assert
    assert body["case_counts"] == {
        "draft": 0,
        "in_review": 0,
        "needs_revision": 0,
        "approved": 0,
    }
    assert body["total_cases"] == 0
    assert body["waiting_for_review"] == 0


def test_jumlah_per_status_dan_menunggu_review(author, db_session):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    _buat(client, suite_id, case_code="DRAFT-001")
    _buat(client, suite_id, case_code="DRAFT-002")
    _ajukan(client, suite_id, "REVIEW-001")

    # Act
    body = _dashboard(client)

    # Assert
    assert body["case_counts"] == {
        "draft": 2,
        "in_review": 1,
        "needs_revision": 0,
        "approved": 0,
    }
    assert body["total_cases"] == 3
    assert body["waiting_for_review"] == 1


def test_masih_menunggu_selama_satu_reviewer_belum_menilai(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A].id)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 1


def test_disetujui_keluar_dari_menunggu_dan_masuk_approved(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.CONTRIBUTOR, reviewer)
        _nilai(client, tugas[reviewer].id)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 0
    assert body["case_counts"]["approved"] == 1
    assert body["case_counts"]["in_review"] == 0


def test_status_mengikuti_versi_yang_berlaku_seperti_daftar_kasus(author, db_session, jadi):
    """Kasus approved dengan draf versi baru tetap dihitung approved, sama seperti daftar kasus."""
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.CONTRIBUTOR, reviewer)
        _nilai(client, tugas[reviewer].id)
    jadi(Role.CONTRIBUTOR, USER_ID_QA)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201

    # Act
    body = _dashboard(client)

    # Assert
    assert body["case_counts"]["approved"] == 1
    assert body["case_counts"]["draft"] == 0
    assert body["total_cases"] == 1


def test_verdict_reviewer_yang_diganti_tidak_dihitung(author, db_session):
    """Round open: A diganti tanpa verdict, B dan C sudah menilai. Tidak ada yang menunggu."""
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B, REVIEWER_C)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    ronde = _round(db_session, case_id)
    tugas = _tugas(db_session, case_id)
    tugas[REVIEWER_A].status = AssignmentStatus.REPLACED
    pengganti = ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER_C)
    db_session.add(pengganti)
    db_session.flush()
    db_session.add_all(
        [
            ReviewVerdict(assignment_id=tugas[REVIEWER_B].id, decision=VerdictDecision.APPROVE),
            ReviewVerdict(assignment_id=pengganti.id, decision=VerdictDecision.APPROVE),
        ]
    )
    db_session.commit()

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 0


def test_reviewer_nonaktif_tetap_dihitung_menunggu(author, db_session):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    tugas[REVIEWER_A].status = AssignmentStatus.VOID
    db_session.add(
        ReviewVerdict(assignment_id=tugas[REVIEWER_B].id, decision=VerdictDecision.APPROVE)
    )
    db_session.commit()

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 1


def test_verdict_reviewer_nonaktif_tidak_sah(author, db_session):
    """A sudah approve lalu dinonaktifkan: slotnya kosong lagi, kasus tetap menunggu."""
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    db_session.add(
        ReviewVerdict(assignment_id=tugas[REVIEWER_A].id, decision=VerdictDecision.APPROVE)
    )
    tugas[REVIEWER_A].status = AssignmentStatus.VOID
    db_session.add(
        ReviewVerdict(assignment_id=tugas[REVIEWER_B].id, decision=VerdictDecision.APPROVE)
    )
    db_session.commit()

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 1


def test_kasus_tanpa_reviewer_dihitung_menunggu_tapi_tidak_di_antrean(author, db_session, jadi):
    """0 reviewer: masuk menunggu review, tidak muncul di antrean reviewer mana pun (AC6)."""
    # Arrange
    client, suite_id = author
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    assert _round(db_session, case_id).status == RoundStatus.AWAITING_ASSIGNMENT
    _reviewer(db_session, REVIEWER_A)
    jadi(Role.CONTRIBUTOR, REVIEWER_A)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 1
    assert body["review_queue"] == []


def test_kasus_dengan_satu_reviewer_dihitung_menunggu(author, db_session):
    """Round menunggu penugasan dengan satu reviewer yang sudah approve tetap menunggu."""
    # Arrange
    client, suite_id = author
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    _reviewer(db_session, REVIEWER_A)
    ronde = _round(db_session, case_id)
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER_A)
    db_session.add(tugas)
    db_session.flush()
    db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
    db_session.commit()

    # Act
    body = _dashboard(client)

    # Assert
    assert body["waiting_for_review"] == 1


# --- AC2: waktu data ------------------------------------------------------------------


def test_generated_at_adalah_waktu_sekarang(author):
    # Arrange
    client, _ = author
    sebelum = datetime.now(UTC)

    # Act
    body = _dashboard(client)

    # Assert
    generated_at = datetime.fromisoformat(body["generated_at"])
    assert generated_at.tzinfo is not None
    assert sebelum - timedelta(seconds=1) <= generated_at <= datetime.now(UTC)


def test_sesi_pengukuran_terakhir_kosong_sampai_pbi11(author):
    # Act
    body = _dashboard(author[0])

    # Assert
    assert body["latest_run"] is None


# --- Bagian khusus peran --------------------------------------------------------------


def test_kontributor_tanpa_tugas_mendapat_antrean_kosong_tanpa_angka_admin(author):
    """PBI-24: antrean ikut ke Kontributor; penulis tanpa tugas melihat daftar kosong."""
    # Act
    body = _dashboard(author[0])

    # Assert
    assert body["review_queue"] == []
    assert body["needs_reassignment"] is None


# --- AC6: antrean reviewer ------------------------------------------------------------


def test_antrean_reviewer_hanya_yang_belum_dinilai_terlama_dulu(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    baru = _ajukan(client, suite_id, "REVIEW-001")
    lama = _ajukan(client, suite_id, "REVIEW-002")
    sudah = _ajukan(client, suite_id, "REVIEW-003")
    # REVIEW-002 diajukan belakangan di tes, tapi round-nya sudah lebih lama menunggu.
    ronde_baru, ronde_lama = _round(db_session, baru), _round(db_session, lama)
    ronde_baru.opened_at = datetime.now(UTC) - timedelta(days=1)
    ronde_lama.opened_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()
    tugas_sudah = _tugas(db_session, sudah)
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    _nilai(client, tugas_sudah[REVIEWER_A].id)

    # Act
    body = _dashboard(client)

    # Assert
    antrean = body["review_queue"]
    assert [baris["case_code"] for baris in antrean] == ["REVIEW-002", "REVIEW-001"]
    assert antrean[0]["case_id"] == lama
    assert antrean[0]["assignment_id"] == str(_tugas(db_session, lama)[REVIEWER_A].id)
    assert antrean[0]["title"] == "PHK sepihak"
    assert antrean[0]["round_no"] == 1
    assert body["needs_reassignment"] is None


def test_antrean_reviewer_tidak_memuat_tugas_reviewer_lain(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    _ajukan(client, suite_id, "REVIEW-001")
    jadi(Role.CONTRIBUTOR, REVIEWER_C)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["review_queue"] == []
    assert body["waiting_for_review"] == 1


def test_antrean_reviewer_tidak_memuat_tugas_yang_diganti(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    _tugas(db_session, case_id)[REVIEWER_A].status = AssignmentStatus.REPLACED
    db_session.commit()
    jadi(Role.CONTRIBUTOR, REVIEWER_A)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["review_queue"] == []


# --- Admin: perlu penugasan ulang -----------------------------------------------------


def test_admin_menghitung_round_tanpa_cukup_reviewer(author, db_session, jadi):
    """Tanpa dua reviewer aktif, round 1 menunggu penugasan Admin (PBI-6 AC8)."""
    # Arrange
    client, suite_id = author
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    assert _round(db_session, case_id).status == RoundStatus.AWAITING_ASSIGNMENT
    jadi(Role.ADMIN, ADMIN)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["needs_reassignment"] == 1
    assert body["waiting_for_review"] == 1
    assert body["review_queue"] is None


def test_admin_menghitung_round_dengan_reviewer_nonaktif(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    _ajukan(client, suite_id, "REVIEW-002")
    _tugas(db_session, case_id)[REVIEWER_A].status = AssignmentStatus.VOID
    db_session.commit()
    jadi(Role.ADMIN, ADMIN)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["needs_reassignment"] == 1
    assert body["waiting_for_review"] == 2


def test_admin_tidak_menghitung_round_yang_sudah_diputuskan(author, db_session, jadi):
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "REVIEW-001")
    tugas = _tugas(db_session, case_id)
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.CONTRIBUTOR, reviewer)
        _nilai(client, tugas[reviewer].id)
    jadi(Role.ADMIN, ADMIN)

    # Act
    body = _dashboard(client)

    # Assert
    assert body["needs_reassignment"] == 0


# --- PBI-24 AC6: pekerjaan menulis Kontributor (own_cases, SCRUM-195) ------------------
# Dashboard Kontributor menampilkan kasus miliknya per status di samping antrean review.
# own_cases menambah, tidak mengganti: case_counts, total_cases, dan waiting_for_review
# tetap menghitung seluruh kasus supaya klien lama tidak berubah.

NOL = {"draft": 0, "in_review": 0, "needs_revision": 0, "approved": 0}


def _setujui(client, db_session, jadi, case_id: str) -> None:
    """Dua reviewer menyetujui round yang berjalan, lalu login kembali sebagai penulis."""
    tugas = _tugas(db_session, case_id)
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.CONTRIBUTOR, reviewer)
        _nilai(client, tugas[reviewer].id)
    jadi(Role.CONTRIBUTOR, USER_ID_QA)


@pytest.fixture
def dua_penulis(author, db_session, jadi):
    """Kontributor A (USER_ID_QA): 1 draft dan 1 approved. Kontributor B: 3 draft.

    Isinya sengaja beda status dan beda pemilik, supaya satu tes membuktikan
    filter pemilik sekaligus hitungan per status.
    """
    client, suite_a = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    _buat(client, suite_a, case_code="A-DRAFT-1")
    _setujui(client, db_session, jadi, _ajukan(client, suite_a, "A-APPROVED-1"))
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_B)
    suite_b = _suite(client, name="Suite Kontributor B")
    for nomor in range(1, 4):
        _buat(client, suite_b, case_code=f"B-DRAFT-{nomor}")
    jadi(Role.CONTRIBUTOR, USER_ID_QA)
    return client


def test_kontributor_hanya_menghitung_kasus_miliknya(dua_penulis):
    """Positif: own_cases A hanya kasus A (2), per status versi yang berlaku (AC6)."""
    # Act
    body = _dashboard(dua_penulis)

    # Assert
    assert body["own_cases"] == {
        "case_counts": {**NOL, "draft": 1, "approved": 1},
        "total_cases": 2,
    }


def test_angka_seluruh_kasus_tidak_berubah(dua_penulis):
    """Positif (regresi): case_counts, total_cases, waiting_for_review tetap semua kasus."""
    # Act
    body = _dashboard(dua_penulis)

    # Assert
    assert body["case_counts"] == {**NOL, "draft": 4, "approved": 1}
    assert body["total_cases"] == 5
    assert body["waiting_for_review"] == 0


def test_kontributor_tanpa_kasus_mendapat_nol_bukan_null(dua_penulis, jadi):
    """Edge: Kontributor yang belum menulis tetap mendapat objek berisi nol.

    Kontributor bisa saja hanya mereview; FE tidak perlu membedakan null dan kosong.
    """
    # Arrange
    jadi(Role.CONTRIBUTOR, REVIEWER_A)

    # Act
    body = _dashboard(dua_penulis)

    # Assert
    assert body["own_cases"] == {"case_counts": NOL, "total_cases": 0}


def test_kasus_milik_sendiri_mengikuti_versi_yang_berlaku(author, db_session, jadi):
    """Edge: kasus approved yang versi barunya sedang in_review tetap dihitung approved.

    Aturannya sama dengan case_counts dan daftar kasus (versi berlaku =
    latest_approved_version_id bila ada).
    """
    # Arrange
    client, suite_id = author
    _reviewer(db_session, REVIEWER_A, REVIEWER_B)
    case_id = _ajukan(client, suite_id, "A-APPROVED-1")
    _setujui(client, db_session, jadi, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200

    # Act
    body = _dashboard(client)

    # Assert
    assert body["own_cases"] == {"case_counts": {**NOL, "approved": 1}, "total_cases": 1}
    assert body["case_counts"]["approved"] == 1


def test_admin_tidak_mendapat_own_cases(dua_penulis, jadi):
    """Negatif: own_cases hanya untuk peran penulis (WRITER_ROLES); Admin mendapat null."""
    # Arrange
    jadi(Role.ADMIN, ADMIN)

    # Act
    body = _dashboard(dua_penulis)

    # Assert
    assert body["own_cases"] is None


# Viewer tetap 403 untuk seluruh dashboard: test_viewer_tidak_punya_dashboard di atas.


# --- Service dengan dependency di-mock --------------------------------------------------
# cases_service dan reviews_service diganti MagicMock, supaya yang diuji hanya keputusan
# dashboard: hitungan milik siapa yang diminta, dan untuk peran apa.


@pytest.fixture
def layanan(monkeypatch):
    cases = MagicMock()
    cases.count_by_status.return_value = dict.fromkeys(CaseStatus, 0)
    reviews = MagicMock()
    reviews.count_cases_waiting.return_value = 0
    reviews.pending_queue.return_value = []
    reviews.count_cases_needing_reassignment.return_value = 0
    monkeypatch.setattr(dashboard_service, "cases_service", cases)
    monkeypatch.setattr(dashboard_service, "reviews_service", reviews)
    return cases


def test_service_meminta_hitungan_milik_kontributor_yang_login(layanan):
    """Positif: hitungan pemilik memakai id pengguna yang login, di samping hitungan semua."""
    # Arrange
    db = MagicMock()

    # Act
    dashboard_service.summary(db, user_id=USER_ID_QA, role=Role.CONTRIBUTOR)

    # Assert
    assert layanan.count_by_status.call_args_list == [
        ((db,), {}),
        ((db,), {"created_by": USER_ID_QA}),
    ]


def test_service_admin_tidak_menghitung_milik_sendiri(layanan):
    """Negatif: untuk Admin hanya hitungan semua kasus yang diminta, tanpa filter pemilik."""
    # Act
    dashboard_service.summary(MagicMock(), user_id=ADMIN, role=Role.ADMIN)

    # Assert
    layanan.count_by_status.assert_called_once()
    assert "created_by" not in layanan.count_by_status.call_args.kwargs
