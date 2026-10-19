"""Blind verdict dan keputusan review lewat HTTP. PBI-6 AC2-AC7, SCRUM-145.

Alur D3 langkah 1-41 dari ujung ke ujung: Author mengajukan, penugasan
otomatis SCRUM-144 memasang Reviewer A dan B lalu membuka round, kemudian
keduanya menilai. Kasus di luar alur itu (penulis yang ditugaskan) masih
menanam assignment langsung lewat _buka_round.
"""

import csv
import io
import uuid
from datetime import timedelta

import pytest
from fastapi import Depends
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import Case
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
)
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


@pytest.fixture
def notifikasi_tersimpan(db_session, monkeypatch):
    """DbNotifier membuka sesi sendiri; arahkan ke database tes."""
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False),
    )


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


@pytest.mark.parametrize(
    ("role", "user_id", "path"),
    [
        pytest.param(Role.REVIEWER, REVIEWER_B, "/reviews/assigned", id="reviewer-daftar-tugas"),
        pytest.param(Role.REVIEWER, REVIEWER_B, "/cases/{id}/review", id="reviewer-review-kasus"),
        pytest.param(
            Role.REVIEWER, REVIEWER_B, "/cases/{id}/versions", id="reviewer-riwayat-versi"
        ),
        pytest.param(Role.AUTHOR, USER_ID_QA, "/cases/{id}", id="penulis-detail-kasus"),
        pytest.param(Role.AUTHOR, USER_ID_QA, "/cases/{id}/review", id="penulis-review-kasus"),
        pytest.param(Role.AUTHOR, USER_ID_QA, "/cases/{id}/versions", id="penulis-riwayat-versi"),
    ],
)
def test_respons_mentah_tidak_memuat_verdict_rekan_selama_round_open(
    kasus, jadi, role, user_id, path
):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    assert _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI).status_code == 201
    jadi(role, user_id)

    # Act
    response = client.get(path.format(id=case_id))

    # Assert
    assert response.status_code == 200
    assert "revise" not in response.text
    assert KOMENTAR_REVISI not in response.text


@pytest.mark.parametrize(
    ("role", "user_id"),
    [
        pytest.param(Role.REVIEWER, REVIEWER_B, id="reviewer"),
        pytest.param(Role.AUTHOR, USER_ID_QA, id="penulis"),
    ],
)
def test_review_kasus_memuat_verdict_rekan_setelah_round_decided(kasus, jadi, role, user_id):
    """Kontrol positif: jalur yang sama membuka verdict begitu round selesai."""
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI)
    jadi(Role.REVIEWER, REVIEWER_B)
    _nilai(client, tugas[REVIEWER_B])
    jadi(role, user_id)

    # Act
    response = client.get(f"/cases/{case_id}/review")

    # Assert
    assert response.status_code == 200
    assert "revise" in response.text
    assert KOMENTAR_REVISI in response.text


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


def _ajukan_ulang_setelah_revisi_b(client, jadi, case_id: str, tugas) -> None:
    """Round 1: A setuju, B minta revisi; Author lalu mengajukan ulang (round 2)."""
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)
    _nilai(client, tugas[REVIEWER_B], "revise", KOMENTAR_REVISI)
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.put(f"/cases/{case_id}", json=_badan()).status_code == 200
    ronde = client.post(f"/cases/{case_id}/submit-review").json()
    assert (ronde["round_no"], ronde["round_status"]) == (2, "open")


def test_approve_yang_dibawa_ke_round_revisi_ikut_disamarkan(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, tugas)
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


@pytest.mark.parametrize("reviewer", [REVIEWER_A, REVIEWER_B], ids=["reviewer-a", "reviewer-b"])
def test_notifikasi_tugas_membuka_penugasan_milik_reviewer_itu(
    notifikasi_tersimpan, kasus, jadi, reviewer
):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, reviewer)

    # Act
    response = client.get("/notifications")

    # Assert
    [notifikasi] = response.json()["items"]
    assert notifikasi["type"] == "review_assigned"
    assert notifikasi["link"] == f"/reviews/{tugas[reviewer]}"


def test_penulis_mendapat_notifikasi_revisi_beserta_komentar(kasus, jadi, notifikasi_tersimpan):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.REVIEWER, REVIEWER_B)
    _nilai(client, tugas[REVIEWER_B], "revise", KOMENTAR_REVISI)
    jadi(Role.AUTHOR, USER_ID_QA)

    # Act
    response = client.get("/notifications")

    # Assert
    assert response.status_code == 200
    [notifikasi] = response.json()["items"]
    assert notifikasi["type"] == "review_decided"
    assert notifikasi["title"] == "PHK-001 perlu revisi"
    assert notifikasi["body"] == KOMENTAR_REVISI
    assert notifikasi["link"] == f"/cases/{case_id}/edit"
    assert notifikasi["read_at"] is None


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


# --- Pengajuan ulang (AC9) ------------------------------------------------------------


def _tugas_milik(client, jadi, reviewer_id: uuid.UUID) -> dict:
    jadi(Role.REVIEWER, reviewer_id)
    [baris] = client.get("/reviews/assigned").json()
    return baris


def test_ajukan_ulang_hanya_peminta_revisi_yang_menilai_lagi(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, tugas)
    tugas_b = _tugas_milik(client, jadi, REVIEWER_B)
    tugas_a = _tugas_milik(client, jadi, REVIEWER_A)

    # Act
    nilai_ulang_a = _nilai(client, tugas_a["assignment_id"], "revise", KOMENTAR_REVISI)

    # Assert
    assert tugas_b["my_verdict"] is None
    assert tugas_a["my_verdict"]["decision"] == "approve"
    assert nilai_ulang_a.status_code == 409


def test_persetujuan_lama_tetap_berlaku_saat_peminta_revisi_setuju(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, tugas)
    tugas_b = _tugas_milik(client, jadi, REVIEWER_B)

    # Act
    response = _nilai(client, tugas_b["assignment_id"])

    # Assert
    assert response.json()["round_status"] == "decided"
    assert response.json()["round_decision"] == "approved"
    assert _status_versi(db_session, case_id) == "approved"


def _komentar_per_round(db_session, case_id: str, reviewer_id: uuid.UUID) -> dict[int, str]:
    baris = (
        db_session.query(ReviewRound.round_no, ReviewVerdict.comment)
        .join(ReviewAssignment, ReviewAssignment.review_round_id == ReviewRound.id)
        .join(ReviewVerdict, ReviewVerdict.assignment_id == ReviewAssignment.id)
        .filter(ReviewRound.case_id == uuid.UUID(case_id))
        .filter(ReviewAssignment.reviewer_id == reviewer_id)
    )
    return dict(baris.all())


def test_ditolak_dua_kali_membuka_round_tiga_dan_komentar_lama_utuh(kasus, jadi, db_session):
    # Arrange
    client, case_id, tugas = kasus
    komentar_kedua = "Ayat juga keliru"
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, tugas)
    tugas_b = _tugas_milik(client, jadi, REVIEWER_B)
    assert _nilai(client, tugas_b["assignment_id"], "revise", komentar_kedua).status_code == 201
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.put(f"/cases/{case_id}", json=_badan()).status_code == 200

    # Act
    response = client.post(f"/cases/{case_id}/submit-review")

    # Assert
    assert response.json()["round_no"] == 3
    assert response.json()["round_status"] == "open"
    assert _tugas_milik(client, jadi, REVIEWER_A)["my_verdict"]["decision"] == "approve"
    assert _komentar_per_round(db_session, case_id, REVIEWER_B) == {
        1: KOMENTAR_REVISI,
        2: komentar_kedua,
    }


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


# --- Pemantauan "Dalam review" untuk Admin (AC3, temuan Pra-UAT F-02) ---------------


def _dalam_review(client) -> list[dict]:
    response = client.get("/reviews/in-review")
    assert response.status_code == 200
    return response.json()


def _setujui_berdua(client, jadi, tugas: dict[uuid.UUID, str]) -> None:
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.REVIEWER, reviewer)
        assert _nilai(client, tugas[reviewer]).status_code == 201


def test_admin_melihat_kasus_yang_sedang_direview(kasus, jadi):
    # Arrange
    client, case_id, tugas = kasus
    jadi(Role.ADMIN, ADMIN)

    # Act
    isi = _dalam_review(client)

    # Assert
    assert len(isi) == 1
    baris = isi[0]
    assert baris["case_id"] == case_id
    assert baris["case_code"] == "PHK-001"
    assert baris["title"] == "PHK sepihak"
    assert baris["version_no"] == 1
    assert baris["review"]["status"] == "open"
    assert {slot["assignment_id"] for slot in baris["review"]["reviewers"]} == set(tugas.values())


def test_versi_baru_dari_kasus_yang_disetujui_ikut_tampil(kasus, jadi):
    """F-02: status kasus tetap approved selama versi 2 direview, tapi rondenya harus terlihat."""
    # Arrange
    client, case_id, tugas = kasus
    _setujui_berdua(client, jadi, tugas)
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    assert client.get(f"/cases/{case_id}").json()["status"] == "approved"
    jadi(Role.ADMIN, ADMIN)

    # Act
    isi = _dalam_review(client)

    # Assert
    assert [(baris["case_id"], baris["version_no"]) for baris in isi] == [(case_id, 2)]
    assert isi[0]["review"]["status"] == "open"
    assert len(isi[0]["review"]["reviewers"]) == 2


@pytest.mark.parametrize("keputusan", ["approve", "revise"])
def test_kasus_yang_rondenya_sudah_diputuskan_tidak_tampil(kasus, jadi, keputusan):
    # Arrange
    client, _, tugas = kasus
    if keputusan == "approve":
        _setujui_berdua(client, jadi, tugas)
    else:
        jadi(Role.REVIEWER, REVIEWER_A)
        assert _nilai(client, tugas[REVIEWER_A], "revise", KOMENTAR_REVISI).status_code == 201
    jadi(Role.ADMIN, ADMIN)

    # Act + Assert
    assert _dalam_review(client) == []


def test_admin_melihat_verdict_yang_sudah_masuk_di_daftar(kasus, jadi):
    # Arrange
    client, _, tugas = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, tugas[REVIEWER_A])
    jadi(Role.ADMIN, ADMIN)

    # Act
    slots = _dalam_review(client)[0]["review"]["reviewers"]

    # Assert
    verdict = {slot["reviewer"]["name"]: slot["verdict"] for slot in slots}
    assert verdict["Reviewer A"]["decision"] == "approve"
    assert verdict["Reviewer B"] is None


def test_round_yang_menunggu_penugasan_ikut_tampil(kasus, jadi, db_session):
    # Arrange
    client, case_id, _ = kasus
    db_session.query(ReviewRound).filter_by(
        case_id=uuid.UUID(case_id)
    ).one().status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.commit()
    jadi(Role.ADMIN, ADMIN)

    # Act
    isi = _dalam_review(client)

    # Assert
    assert [baris["review"]["status"] for baris in isi] == ["awaiting_assignment"]


def test_ronde_yang_paling_lama_menunggu_tampil_lebih_dulu(kasus, jadi, db_session):
    # Arrange
    client, case_lama, _ = kasus
    case_baru = _buat(client, _suite(client, name="Suite kedua"), case_code="PHK-002").json()["id"]
    assert client.post(f"/cases/{case_baru}/submit-review").status_code == 200
    rondes = {str(r.case_id): r for r in db_session.query(ReviewRound).all()}
    rondes[case_lama].opened_at = rondes[case_baru].opened_at - timedelta(days=1)
    db_session.commit()
    jadi(Role.ADMIN, ADMIN)

    # Act
    isi = _dalam_review(client)

    # Assert
    assert [baris["case_id"] for baris in isi] == [case_lama, case_baru]


def test_daftar_kosong_bila_tidak_ada_kasus_yang_direview(as_role):
    # Arrange
    client = as_role(Role.ADMIN)

    # Act + Assert
    assert _dalam_review(client) == []


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_hanya_admin_yang_membuka_daftar_dalam_review(kasus, jadi, role):
    # Arrange
    client, _, _ = kasus
    jadi(role, uuid.uuid4())

    # Act
    response = client.get("/reviews/in-review")

    # Assert
    assert response.status_code == 403


def test_daftar_dalam_review_tanpa_sesi_ditolak(client):
    # Act + Assert
    assert client.get("/reviews/in-review").status_code == 401


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
