"""Blind verdict dan keputusan review lewat HTTP. PBI-6 AC2-AC7, SCRUM-145.

Alur D3 langkah 1-41 dari ujung ke ujung: Author mengajukan, penugasan
otomatis SCRUM-144 memasang Reviewer A dan B lalu membuka round, kemudian
keduanya menilai. Kasus di luar alur itu (penulis yang ditugaskan) masih
menanam assignment langsung lewat _buka_round.
"""

import csv
import io
import uuid

import pytest
from fastapi import Depends

from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import Case
from app.modules.reviews.models import AssignmentStatus, ReviewAssignment, ReviewRound, RoundStatus
from app.shared import request_context
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import _badan, _buat, _suite
from tests.modules.conftest import USER_ID_QA

REVIEWER_A = uuid.UUID("00000000-0000-0000-0000-0000000145a1")
REVIEWER_B = uuid.UUID("00000000-0000-0000-0000-0000000145a2")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000145ad")
VERDICT_SUBMITTED = "review.verdict_submitted"
KOMENTAR_REVISI = "Pasal tidak sesuai"


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
def kasus(as_role, db_session):
    """Kasus milik Author USER_ID_QA yang sudah diajukan, round 1 open dengan A dan B."""
    client = as_role(Role.AUTHOR)
    db_session.add_all(
        [
            User(id=REVIEWER_A, email="a@veritask.test", name="Reviewer A", role=Role.REVIEWER),
            User(id=REVIEWER_B, email="b@veritask.test", name="Reviewer B", role=Role.REVIEWER),
        ]
    )
    db_session.commit()
    case_id = _buat(client, _suite(client)).json()["id"]
    diajukan = client.post(f"/cases/{case_id}/submit-review")
    assert diajukan.status_code == 200
    assert diajukan.json()["round_status"] == "open"
    ronde = db_session.query(ReviewRound).filter_by(case_id=uuid.UUID(case_id)).one()
    tugas = db_session.query(ReviewAssignment).filter_by(review_round_id=ronde.id).all()
    assert {t.reviewer_id for t in tugas} == {REVIEWER_A, REVIEWER_B}
    return client, case_id, {t.reviewer_id: str(t.id) for t in tugas}


def _buka_round(db_session, case_id: str, reviewers: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    ronde = db_session.query(ReviewRound).filter_by(case_id=uuid.UUID(case_id)).one()
    ronde.status = RoundStatus.OPEN
    baris = [ReviewAssignment(review_round_id=ronde.id, reviewer_id=r) for r in reviewers]
    db_session.add_all(baris)
    db_session.commit()
    return {tugas.reviewer_id: str(tugas.id) for tugas in baris}


def _nilai(client, assignment_id: str, decision: str = "approve", comment: str | None = None):
    return client.post(
        f"/reviews/{assignment_id}/verdict", json={"decision": decision, "comment": comment}
    )


def _status_versi(db_session, case_id: str) -> str:
    db_session.expire_all()
    return db_session.get(Case, uuid.UUID(case_id)).current_version.status.value


def _verdict_terlihat(client, case_id: str) -> dict[str, dict | None]:
    body = client.get(f"/cases/{case_id}/review").json()
    return {slot["reviewer"]["name"]: slot["verdict"] for slot in body["reviewers"]}


# --- Dashboard reviewer ----------------------------------------------------------------


def test_reviewer_melihat_kasus_yang_ditugaskan_beserta_isinya(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = client.get("/reviews/assigned")

    # Assert
    assert response.status_code == 200
    [baris] = response.json()
    assert baris["assignment_id"] == tugas[REVIEWER_A]
    assert baris["case"]["case_id"] == case_id
    assert baris["case"]["identity"]["title"] == "PHK sepihak"
    assert baris["case"]["split_tag"] == "dev"
    assert baris["my_verdict"] is None


def test_reviewer_lain_tidak_melihat_tugas_orang(kasus, jadi):
    # Arrange
    client, _, _ = kasus
    jadi(Role.REVIEWER, uuid.uuid4())

    # Act + Assert
    assert client.get("/reviews/assigned").json() == []


# --- Blind (AC3) -----------------------------------------------------------------------


def test_verdict_pertama_tersembunyi_dari_rekan_dan_penulis(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = _nilai(client, tugas[REVIEWER_A])

    # Assert
    assert response.status_code == 201
    assert response.json()["round_status"] == "open"
    assert _verdict_terlihat(client, case_id)["Reviewer A"]["decision"] == "approve"
    jadi(Role.REVIEWER, REVIEWER_B)
    assert _verdict_terlihat(client, case_id) == {"Reviewer A": None, "Reviewer B": None}
    jadi(Role.AUTHOR, USER_ID_QA)
    assert _verdict_terlihat(client, case_id) == {"Reviewer A": None, "Reviewer B": None}


def _baca_audit(client, jalur: str, case_id: str, log_id: int):
    match jalur:
        case "daftar":
            return client.get("/audit-logs", params={"case_id": case_id})
        case "detail":
            return client.get(f"/audit-logs/{log_id}")
        case _:
            return client.get("/audit-logs/export", params={"format": "csv", "case_id": case_id})


def _sesudah_verdict(response, jalur: str, log_id: int) -> dict | str | None:
    """Kolom after baris verdict di respons itu; sel CSV kosong dibaca None."""
    match jalur:
        case "daftar":
            items = response.json()["items"]
            return next(item["after"] for item in items if item["id"] == log_id)
        case "detail":
            return response.json()["after"]
        case _:
            baris = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
            return next(b["Sesudah"] for b in baris if b["Aksi"] == VERDICT_SUBMITTED) or None


@pytest.mark.parametrize("jalur", ["daftar", "detail", "ekspor-csv"])
def test_reviewer_tidak_membaca_verdict_rekan_lewat_audit_log(kasus, jadi, db_session, jalur):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    assert _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI).status_code == 201
    log_id = (
        db_session.query(AuditLog.id)
        .filter_by(case_id=uuid.UUID(case_id), action=VERDICT_SUBMITTED)
        .scalar()
    )
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    response = _baca_audit(client, jalur, case_id, log_id)

    # Assert
    assert response.status_code == 200
    assert _sesudah_verdict(response, jalur, log_id) is None
    assert "revise" not in response.text


def _isi_audit(client, case_id: str, action: str) -> list[dict | None]:
    items = client.get("/audit-logs", params={"case_id": case_id}).json()["items"]
    return [item["after"] for item in items if item["action"] == action]


def test_baris_verdict_rekan_tetap_ada_di_audit_tanpa_isinya(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI)
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    isi = _isi_audit(client, case_id, VERDICT_SUBMITTED)

    # Assert
    assert isi == [None]


@pytest.mark.parametrize(
    ("role", "pembaca"),
    [(Role.REVIEWER, REVIEWER_A), (Role.ADMIN, ADMIN)],
    ids=["pemilik-verdict", "admin"],
)
def test_verdict_di_audit_terbuka_untuk_pemilik_dan_admin_selama_round_open(
    kasus, jadi, role, pembaca
):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI)
    jadi(role, pembaca)

    # Act
    isi = _isi_audit(client, case_id, VERDICT_SUBMITTED)

    # Assert
    assert isi == [{"decision": "revise"}]


def test_verdict_rekan_di_audit_terbuka_setelah_round_decided(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI)
    jadi(Role.REVIEWER, REVIEWER_B)
    _nilai(client, tugas[REVIEWER_B])

    # Act
    isi = _isi_audit(client, case_id, VERDICT_SUBMITTED)

    # Assert
    assert sorted(after["decision"] for after in isi) == ["approve", "revise"]


def test_approve_yang_dibawa_ke_round_revisi_ikut_disamarkan(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)
    _nilai(client, tugas[REVIEWER_B], "revise", KOMENTAR_REVISI)
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.put(f"/cases/{case_id}", json=_badan()).status_code == 200
    assert client.post(f"/cases/{case_id}/submit-review").json()["round_status"] == "open"
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    isi = _isi_audit(client, case_id, "review.approval_carried")

    # Assert
    assert isi == [None]


def test_admin_melihat_verdict_selama_round_open(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.ADMIN, ADMIN)

    # Act
    terlihat = _verdict_terlihat(client, case_id)

    # Assert
    assert terlihat["Reviewer A"]["decision"] == "approve"


# --- Keputusan (AC4-AC6) --------------------------------------------------------------


def test_dua_approve_menyetujui_kasus(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    response = _nilai(client, tugas[REVIEWER_B])

    # Assert
    assert response.json()["round_status"] == "decided"
    assert response.json()["round_decision"] == "approved"
    assert _status_versi(db_session, case_id) == "approved"
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.get(f"/cases/{case_id}").json()["status"] == "approved"
    assert client.get("/reviews/assigned").status_code == 403


def test_satu_revise_mengembalikan_kasus_dengan_komentar(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    response = _nilai(client, tugas[REVIEWER_B], "revise", "Pasal 151 sudah diubah")

    # Assert
    assert response.json()["round_decision"] == "revise"
    assert _status_versi(db_session, case_id) == "needs_revision"
    jadi(Role.AUTHOR, USER_ID_QA)
    terlihat = _verdict_terlihat(client, case_id)
    assert terlihat["Reviewer B"]["comment"] == "Pasal 151 sudah diubah"
    assert terlihat["Reviewer A"]["decision"] == "approve"
    jadi(Role.REVIEWER, REVIEWER_A)
    assert client.get("/reviews/assigned").json() == []


def test_keputusan_tercatat_di_audit_atas_nama_reviewer_kedua(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)

    # Act
    _nilai(client, tugas[REVIEWER_B])

    # Assert
    [baris] = db_session.query(AuditLog).filter_by(action="case.approved").all()
    assert str(baris.case_id) == case_id
    assert baris.actor_user_id == REVIEWER_B


# --- Penolakan --------------------------------------------------------------------------


def test_revise_tanpa_komentar_ditolak(kasus, jadi):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = _nilai(client, tugas[REVIEWER_A], "revise", "  ")

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "COMMENT_REQUIRED"


def test_menilai_dua_kali_ditolak(kasus, jadi):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])

    # Act
    response = _nilai(client, tugas[REVIEWER_A], "revise", "berubah pikiran")

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "VERDICT_ALREADY_SUBMITTED"


def test_menilai_tugas_reviewer_lain_ditolak(kasus, jadi):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = _nilai(client, tugas[REVIEWER_B])

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_YOUR_ASSIGNMENT"


@pytest.mark.parametrize("status", [AssignmentStatus.REPLACED, AssignmentStatus.VOID])
def test_tugas_yang_diganti_atau_dibatalkan_ditolak(kasus, jadi, db_session, status):
    # Arrange
    client, _, tugas = kasus
    db_session.get(ReviewAssignment, uuid.UUID(tugas[REVIEWER_A])).status = status
    db_session.commit()
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = _nilai(client, tugas[REVIEWER_A])

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "ASSIGNMENT_NOT_ACTIVE"


def test_penulis_yang_ditugaskan_tidak_boleh_menilai(as_role, db_session, jadi):
    """AC2: misalnya penulis yang kemudian dijadikan reviewer."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    client.post(f"/cases/{case_id}/submit-review")
    tugas = _buka_round(db_session, case_id, [USER_ID_QA, REVIEWER_B])
    jadi(Role.REVIEWER, USER_ID_QA)

    # Act
    response = _nilai(client, tugas[USER_ID_QA])

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "REVIEWER_IS_AUTHOR"


def test_round_yang_menunggu_penugasan_belum_menerima_verdict(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    db_session.query(ReviewRound).filter_by(
        case_id=uuid.UUID(case_id)
    ).one().status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = _nilai(client, tugas[REVIEWER_A])

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "ROUND_NOT_OPEN"


def test_tugas_tidak_dikenal(kasus, jadi):
    # Arrange
    client, _, _ = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act + Assert
    assert _nilai(client, str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.ADMIN, Role.VIEWER])
def test_hanya_reviewer_yang_menilai(kasus, jadi, role):
    # Arrange
    client, _, tugas = kasus
    jadi(role, uuid.uuid4())

    # Act + Assert
    assert _nilai(client, tugas[REVIEWER_A]).status_code == 403


def test_viewer_tidak_melihat_review_kasus(kasus, jadi):
    # Arrange
    client, case_id, _ = kasus
    jadi(Role.VIEWER, uuid.uuid4())

    # Act + Assert
    assert client.get(f"/cases/{case_id}/review").status_code == 403


def test_review_kasus_yang_belum_diajukan(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = client.get(f"/cases/{case_id}/review")

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "NO_REVIEW_ROUND"


def test_tanpa_sesi_ditolak(client):
    # Act + Assert
    assert client.get("/reviews/assigned").status_code == 401


# --- Tag hanya informasi (PBI-9 AC2, SCRUM-164) --------------------------------------


def test_verdict_tidak_menerima_tag(kasus, jadi):
    """Reviewer menilai substansi kasus, bukan tag dev/test."""
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = client.post(
        f"/reviews/{tugas[REVIEWER_A]}/verdict",
        json={"decision": "approve", "split_tag": "test"},
    )

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "SPLIT_TAG_NOT_REVIEWED"
    assert response.json()["field"] == "split_tag"
    assert client.get("/reviews/assigned").json()[0]["my_verdict"] is None


def test_body_penilaian_lain_tetap_pakai_error_bawaan_tim(kasus, jadi):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)

    # Act
    response = client.post(f"/reviews/{tugas[REVIEWER_A]}/verdict", json={"decision": "tolak"})

    # Assert
    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "decision harus approve atau revise.",
        "field": "decision",
    }
