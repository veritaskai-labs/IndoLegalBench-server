"""Peran Kontributor lewat HTTP. PBI-24 AC1-AC3 dan AC5, SCRUM-195.

Satu peran menulis dan mereview (D10). Penulis dan reviewer kini satu
peran, jadi pembeda keduanya adalah relasi: users.id penulis kasus
(cases.created_by) atau reviewer assignment, bukan peran akun (D11).
"""

import uuid

import pytest

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.reviews.models import ReviewAssignment, ReviewCommentReply, ReviewRound
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _suite
from tests.modules.conftest import USER_ID_QA
from tests.modules.reviews.test_review_api import _nilai, _status_versi
from tests.modules.reviews.test_review_api import jadi as jadi

KONTRIBUTOR_A = uuid.UUID("00000000-0000-0000-0000-0000000195a1")
KONTRIBUTOR_B = uuid.UUID("00000000-0000-0000-0000-0000000195a2")
KONTRIBUTOR_C = uuid.UUID("00000000-0000-0000-0000-0000000195a3")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000195ad")


@pytest.fixture(autouse=True)
def tanpa_notifikasi_penugasan(monkeypatch):
    # DbNotifier membuka sesinya sendiri; tes ini tidak memeriksa notifikasi penugasan.
    monkeypatch.setattr("app.modules.notifications.service.DbNotifier.notify", lambda *args: None)


@pytest.fixture
def diajukan(as_role, db_session):
    """Kasus milik Kontributor USER_ID_QA yang sudah diajukan untuk direview."""
    client = as_role(Role.CONTRIBUTOR)
    db_session.add_all(
        [
            # Penulisnya sendiri juga Kontributor aktif, jadi ikut di daftar peran.
            User(
                id=USER_ID_QA, email="penulis@veritask.test", name="Penulis", role=Role.CONTRIBUTOR
            ),
            User(
                id=KONTRIBUTOR_A,
                email="ka@veritask.test",
                name="Kontributor A",
                role=Role.CONTRIBUTOR,
            ),
            User(
                id=KONTRIBUTOR_B,
                email="kb@veritask.test",
                name="Kontributor B",
                role=Role.CONTRIBUTOR,
            ),
        ]
    )
    db_session.commit()
    case_id = _buat(client, _suite(client)).json()["id"]
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    ronde = db_session.query(ReviewRound).filter_by(case_id=uuid.UUID(case_id)).one()
    tugas = db_session.query(ReviewAssignment).filter_by(review_round_id=ronde.id).all()
    return client, case_id, {t.reviewer_id: str(t.id) for t in tugas}


def test_kontributor_menulis_lalu_dua_kontributor_lain_ditugaskan(diajukan):
    """AC2, AC3, AC5: penulis tidak pernah terpilih walau perannya sama."""
    # Arrange + Act
    _, _, tugas = diajukan

    # Assert
    assert set(tugas) == {KONTRIBUTOR_A, KONTRIBUTOR_B}


def test_kurang_dari_dua_kontributor_selain_penulis_masuk_antrean_admin(as_role, db_session):
    """AC5: penulis tidak dipakai untuk menutup kekurangan kandidat."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    db_session.add_all(
        [
            User(id=USER_ID_QA, email="p@veritask.test", name="Penulis", role=Role.CONTRIBUTOR),
            User(id=KONTRIBUTOR_A, email="a@veritask.test", name="A", role=Role.CONTRIBUTOR),
        ]
    )
    db_session.commit()
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = client.post(f"/cases/{case_id}/submit-review")

    # Assert
    assert response.status_code == 200
    assert response.json()["round_status"] == "awaiting_assignment"
    assert db_session.query(ReviewAssignment).count() == 0


def test_kontributor_menilai_kasus_orang_lain_sampai_disetujui(diajukan, jadi, db_session):
    """AC2: wewenang Reviewer lama ada di Kontributor."""
    # Arrange
    client, case_id, tugas = diajukan

    # Act
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)
    pertama = _nilai(client, tugas[KONTRIBUTOR_A])
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_B)
    kedua = _nilai(client, tugas[KONTRIBUTOR_B])

    # Assert
    assert (pertama.status_code, kedua.status_code) == (201, 201)
    assert _status_versi(db_session, case_id) == "approved"


def test_penulis_kontributor_tidak_bisa_menilai_kasusnya_sendiri(diajukan):
    """AC3-AC4: ditolak di server, bukan hanya disembunyikan."""
    # Arrange
    client, _, tugas = diajukan

    # Act
    response = _nilai(client, tugas[KONTRIBUTOR_A])

    # Assert
    assert response.status_code == 403


def test_kontributor_melihat_antrean_review_di_dashboard(diajukan, jadi):
    """Antrean review Reviewer lama ikut ke Kontributor (PBI-19)."""
    # Arrange
    client, case_id, _ = diajukan
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)

    # Act
    antrean = client.get("/dashboard").json()["review_queue"]

    # Assert
    assert [baris["case_id"] for baris in antrean] == [case_id]


def test_kontributor_membaca_audit_kasus_yang_ditugaskan(diajukan, jadi):
    """Cakupan audit Reviewer lama (PBI-18 AC5) ikut ke Kontributor."""
    # Arrange
    client, _, _ = diajukan
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)

    # Act
    response = client.get("/audit-logs")

    # Assert
    assert response.status_code == 200


def test_admin_mengganti_reviewer_dengan_kontributor_lain(diajukan, jadi, db_session):
    """AC2: pengganti harus Kontributor aktif yang bukan penulis (PBI-6 AC7)."""
    # Arrange
    client, _, tugas = diajukan
    db_session.add(
        User(
            id=KONTRIBUTOR_C, email="kc@veritask.test", name="Kontributor C", role=Role.CONTRIBUTOR
        )
    )
    db_session.commit()
    jadi(Role.ADMIN, ADMIN)

    # Act
    response = client.post(
        f"/reviews/{tugas[KONTRIBUTOR_A]}/replace",
        json={"new_reviewer_id": str(KONTRIBUTOR_C), "reason": "Sedang cuti"},
    )

    # Assert
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("role", "kode"),
    [(Role.VIEWER, "INVALID_REVIEWER"), (Role.CONTRIBUTOR, "CANNOT_ASSIGN_AUTHOR")],
    ids=["bukan-kontributor", "penulis-kasus"],
)
def test_admin_tidak_bisa_menugaskan_viewer_atau_penulis(diajukan, jadi, db_session, role, kode):
    """AC3: penulis ditolak walau perannya Kontributor; Viewer tidak bisa mereview."""
    # Arrange
    client, _, tugas = diajukan
    pengganti = USER_ID_QA if role == Role.CONTRIBUTOR else KONTRIBUTOR_C
    if role == Role.VIEWER:
        db_session.add(User(id=KONTRIBUTOR_C, email="v@veritask.test", name="V", role=Role.VIEWER))
        db_session.commit()
    jadi(Role.ADMIN, ADMIN)

    # Act
    response = client.post(
        f"/reviews/{tugas[KONTRIBUTOR_A]}/replace",
        json={"new_reviewer_id": str(pengganti), "reason": "Uji"},
    )

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == kode


def test_balasan_diskusi_kontributor_dicatat_sesuai_relasinya(diajukan, jadi, db_session):
    """K-03: author_role diskusi berarti penulis atau peninjau, bukan peran akun.

    Tanpa ini, balasan Kontributor melanggar CHECK ck_review_reply_author_role.
    """
    # Arrange
    client, _, tugas = diajukan
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)
    _nilai(client, tugas[KONTRIBUTOR_A], "revise", "Mohon perjelas rujukan.")
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_B)
    _nilai(client, tugas[KONTRIBUTOR_B])
    url = f"/reviews/{tugas[KONTRIBUTOR_A]}/replies"

    # Act
    jadi(Role.CONTRIBUTOR, USER_ID_QA)
    dari_penulis = client.post(url, json={"body": "Bagian mana?"})
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)
    dari_peninjau = client.post(url, json={"body": "Pasal 156."})

    # Assert
    assert (dari_penulis.status_code, dari_peninjau.status_code) == (201, 201)
    assert [b.author_role for b in db_session.query(ReviewCommentReply).all()] == [
        "author",
        "reviewer",
    ]
    judul = [
        n.title for n in db_session.query(Notification).filter_by(type="review_comment_replied")
    ]
    assert judul == ["Balasan penulis pada komentar Anda", "Balasan peninjau pada diskusi Anda"]


def test_kontributor_lain_tidak_bisa_membalas_diskusi(diajukan, jadi, db_session):
    """Peran Kontributor saja tidak cukup; harus penulis atau pemilik komentar."""
    # Arrange
    client, _, tugas = diajukan
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_A)
    _nilai(client, tugas[KONTRIBUTOR_A], "revise", "Komentar A")
    jadi(Role.CONTRIBUTOR, KONTRIBUTOR_B)
    _nilai(client, tugas[KONTRIBUTOR_B])

    # Act
    response = client.post(f"/reviews/{tugas[KONTRIBUTOR_A]}/replies", json={"body": "Ikut"})

    # Assert
    assert response.status_code == 403
    assert db_session.query(ReviewCommentReply).count() == 0
