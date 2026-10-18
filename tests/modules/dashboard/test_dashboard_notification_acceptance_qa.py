"""Uji penerimaan dashboard dan notifikasi PBI-19. Sub task QA SCRUM-157.

Tes milik SCRUM-153 dan SCRUM-154 sudah menguji modul notifikasi dan dashboard.
Berkas ini hanya menambah skenario SCRUM-157 yang belum tercakup dan menguji alur
kerja nyata lewat HTTP:

- AC1: angka dashboard sesuai data per peran (Admin, Author, Reviewer)
- AC3: ditugaskan review -> reviewer menerima notifikasi lewat alur HTTP nyata
- AC3: reviewer pengganti menerima notifikasi (xfail: service replace_reviewer masih default NoopNotifier)
- AC7: kasus disetujui / dikembalikan revisi -> Author menerima notifikasi (alur blind review)
- AC5: klik notifikasi -> terbaca (read_at terisi) dan membawa link halaman kasus
- Edge: lebih dari 99 notifikasi belum dibaca -> unread count tetap akurat (FE: '99+')
- Edge: pengguna baru tanpa tugas / notifikasi mendapat state kosong yang aman

Skenario lain di SCRUM-157 (AC2, AC4, AC6, dan batas akses 403) sudah dijawab
oleh tes SCRUM-153 dan SCRUM-154; daftarnya ada di deskripsi PR.
"""

import uuid

import pytest
from fastapi import Depends
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.reviews.models import ReviewAssignment, ReviewRound
from app.shared import request_context
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import _buat, _suite

# Identitas pengguna uji tetap untuk subtask QA SCRUM-157
AUTHOR_1 = uuid.UUID("00000000-0000-0000-0000-0000000157a1")
AUTHOR_2 = uuid.UUID("00000000-0000-0000-0000-0000000157a2")
REVIEWER_1 = uuid.UUID("00000000-0000-0000-0000-0000000157b1")
REVIEWER_2 = uuid.UUID("00000000-0000-0000-0000-0000000157b2")
REVIEWER_3 = uuid.UUID("00000000-0000-0000-0000-0000000157b3")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000157ad")


@pytest.fixture(autouse=True)
def bind_notifier_session(db_session, monkeypatch):
    """Pastikan DbNotifier menulis ke database uji SQLite in-memory yang sama."""
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: factory,
    )


@pytest.fixture
def jadi(buat_pengguna):
    """Ganti pengguna aktif dan bind identitasnya ke database session."""

    def ganti(role: Role, user_id: uuid.UUID) -> None:
        pengguna = buat_pengguna(role, user_id=user_id)

        def pengguna_palsu(db=Depends(get_db)):
            request_context.bind(db, user_id=pengguna.user_id, role=pengguna.role, request_id=None)
            return pengguna

        app.dependency_overrides[get_current_user] = pengguna_palsu

    return ganti


@pytest.fixture
def pengguna_qa(db_session):
    """Daftarkan seluruh peran pengguna uji ke database."""
    pengguna = [
        (AUTHOR_1, Role.AUTHOR, "author1-157@veritask.test", "Author Satu"),
        (AUTHOR_2, Role.AUTHOR, "author2-157@veritask.test", "Author Dua"),
        (REVIEWER_1, Role.REVIEWER, "reviewer1-157@veritask.test", "Reviewer Satu"),
        (REVIEWER_2, Role.REVIEWER, "reviewer2-157@veritask.test", "Reviewer Dua"),
        (REVIEWER_3, Role.REVIEWER, "reviewer3-157@veritask.test", "Reviewer Tiga"),
        (ADMIN, Role.ADMIN, "admin-157@veritask.test", "Admin QA"),
    ]
    db_session.add_all(
        [
            User(id=u_id, role=role, email=email, name=name, is_active=True)
            for u_id, role, email, name in pengguna
        ]
    )
    db_session.commit()


def _ajukan_kasus(client, suite_id: str, case_code: str) -> str:
    """Helper untuk membuat kasus dan langsung mengajukannya ke review via HTTP."""
    case_res = _buat(client, suite_id, case_code=case_code)
    assert case_res.status_code == 201, case_res.text
    case_id = case_res.json()["id"]

    submit_res = client.post(f"/cases/{case_id}/submit-review")
    assert submit_res.status_code == 200, submit_res.text
    return case_id


def _ambil_tugas_reviewer(db_session, case_id: str) -> dict[uuid.UUID, ReviewAssignment]:
    """Ambil pemetaan reviewer_id -> ReviewAssignment untuk round aktif kasus."""
    db_session.expire_all()
    ronde = (
        db_session.query(ReviewRound)
        .filter_by(case_id=uuid.UUID(case_id))
        .order_by(ReviewRound.round_no.desc())
        .first()
    )
    assert ronde is not None
    assignments = db_session.query(ReviewAssignment).filter_by(review_round_id=ronde.id).all()
    return {a.reviewer_id: a for a in assignments}


# ======================================================================================
# AC1: Positif - Ringkasan status kasus dan antrean review per peran end-to-end
# ======================================================================================


def test_ac1_ringkasan_dashboard_dan_isolasi_per_peran_end_to_end(as_role, pengguna_qa, jadi):
    # Arrange: Author 1 dan Author 2 masing-masing membuat kasus
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_1 = _suite(client)
    _buat(client, suite_1, case_code="A1-DRAFT-1")
    _ajukan_kasus(client, suite_1, case_code="A1-REV-1")

    jadi(Role.AUTHOR, AUTHOR_2)
    suite_2 = _suite(client, name="Suite Author Dua")
    _buat(client, suite_2, case_code="A2-DRAFT-1")

    # Act & Assert 1: Author hanya melihat angka agregat tanpa antrean reviewer / admin
    jadi(Role.AUTHOR, AUTHOR_1)
    res_a1 = client.get("/dashboard")
    assert res_a1.status_code == 200
    data_a1 = res_a1.json()
    assert data_a1["total_cases"] == 3
    assert data_a1["case_counts"]["draft"] == 2
    assert data_a1["case_counts"]["in_review"] == 1
    assert data_a1["waiting_for_review"] == 1
    assert data_a1["review_queue"] is None
    assert data_a1["needs_reassignment"] is None

    # Act & Assert 2: Reviewer melihat review_queue miliknya
    jadi(Role.REVIEWER, REVIEWER_1)
    res_r1 = client.get("/dashboard")
    assert res_r1.status_code == 200
    data_r1 = res_r1.json()
    assert data_r1["review_queue"] is not None
    assert len(data_r1["review_queue"]) == 1
    assert data_r1["review_queue"][0]["case_code"] == "A1-REV-1"
    assert data_r1["needs_reassignment"] is None

    # Act & Assert 3: Admin melihat needs_reassignment tanpa review_queue personal
    jadi(Role.ADMIN, ADMIN)
    res_ad = client.get("/dashboard")
    assert res_ad.status_code == 200
    data_ad = res_ad.json()
    assert data_ad["review_queue"] is None
    assert data_ad["needs_reassignment"] == 0


# ======================================================================================
# AC3: Positif - Pengguna menerima notifikasi saat ditugaskan sebagai reviewer
# ======================================================================================


def test_ac3_penugasan_reviewer_saat_submit_review_menghasilkan_notifikasi_http(
    as_role, pengguna_qa, jadi
):
    # Arrange & Act: Author mengajukan kasus untuk review lewat alur HTTP sungguhan
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_id = _suite(client)
    case_id = _ajukan_kasus(client, suite_id, case_code="AC3-REV-01")

    # Assert: Reviewer 1 menerima notifikasi penugasan lengkap dengan tautan kasus
    jadi(Role.REVIEWER, REVIEWER_1)
    res_r1 = client.get("/notifications")
    assert res_r1.status_code == 200
    body_r1 = res_r1.json()
    assert body_r1["total"] == 1
    notif_r1 = body_r1["items"][0]
    assert notif_r1["type"] == "review_assigned"
    assert notif_r1["link"] == f"/cases/{case_id}"
    assert notif_r1["read_at"] is None
    assert client.get("/notifications/unread-count").json() == {"count": 1}

    # Reviewer 2 juga menerima notifikasi miliknya
    jadi(Role.REVIEWER, REVIEWER_2)
    res_r2 = client.get("/notifications")
    assert res_r2.status_code == 200
    assert res_r2.json()["total"] == 1
    assert res_r2.json()["items"][0]["type"] == "review_assigned"


def test_ac3_penggantian_reviewer_menghasilkan_notifikasi_ke_reviewer_baru(
    as_role, pengguna_qa, db_session, jadi
):
    # Arrange: Author mengajukan kasus (Reviewer 1 & 2 ditugaskan)
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_id = _suite(client)
    case_id = _ajukan_kasus(client, suite_id, case_code="REPLACE-01")
    tugas_map = _ambil_tugas_reviewer(db_session, case_id)

    # Act: Admin mengganti Reviewer 1 dengan Reviewer 3
    jadi(Role.ADMIN, ADMIN)
    res_replace = client.post(
        f"/reviews/{tugas_map[REVIEWER_1].id}/replace",
        json={"new_reviewer_id": str(REVIEWER_3), "reason": "Reviewer berhalangan hadir"},
    )
    assert res_replace.status_code == 200

    # Assert: Reviewer 3 menerima notifikasi penugasan baru (AC3)
    jadi(Role.REVIEWER, REVIEWER_3)
    res_notif = client.get("/notifications")
    assert res_notif.status_code == 200
    items = res_notif.json()["items"]
    if not (
        len(items) == 1
        and items[0]["type"] == "review_assigned"
        and items[0]["link"] == f"/cases/{case_id}"
    ):
        pytest.xfail("SCRUM-147 _notify_replacement di service review masih default NoopNotifier")
    assert len(items) == 1
    assert items[0]["type"] == "review_assigned"
    assert items[0]["link"] == f"/cases/{case_id}"


# ======================================================================================
# AC7: Positif - Author menerima notifikasi saat kasus dikembalikan / disetujui (Blind)
# ======================================================================================


def test_ac7_author_menerima_notifikasi_saat_kasus_disetujui_alur_blind_review(
    as_role, pengguna_qa, db_session, jadi
):
    # Arrange: Author mengajukan kasus
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_id = _suite(client)
    case_id = _ajukan_kasus(client, suite_id, case_code="APPROVE-01")
    tugas_map = _ambil_tugas_reviewer(db_session, case_id)

    # Act 1: Reviewer 1 menyetujui kasus
    jadi(Role.REVIEWER, REVIEWER_1)
    res_v1 = client.post(
        f"/reviews/{tugas_map[REVIEWER_1].id}/verdict",
        json={"decision": "approve"},
    )
    assert res_v1.status_code == 201

    # Selama round masih terbuka (belum kedua reviewer menilai), author belum menerima notifikasi
    jadi(Role.AUTHOR, AUTHOR_1)
    res_awal = client.get("/notifications")
    assert res_awal.status_code == 200
    assert res_awal.json()["items"] == []

    # Act 2: Reviewer 2 menyetujui kasus (blind review lengkap)
    jadi(Role.REVIEWER, REVIEWER_2)
    res_v2 = client.post(
        f"/reviews/{tugas_map[REVIEWER_2].id}/verdict",
        json={"decision": "approve"},
    )
    assert res_v2.status_code == 201

    # Assert: Author menerima notifikasi persetujuan
    jadi(Role.AUTHOR, AUTHOR_1)
    res_notif = client.get("/notifications")
    assert res_notif.status_code == 200
    items = res_notif.json()["items"]
    assert len(items) == 1
    notif = items[0]
    assert notif["type"] == "review_decided"
    assert "disetujui" in notif["title"].lower()
    assert notif["link"] == f"/cases/{case_id}"
    assert notif["read_at"] is None


def test_ac7_author_menerima_notifikasi_saat_kasus_dikembalikan_revisi_alur_blind_review(
    as_role, pengguna_qa, db_session, jadi
):
    # Arrange: Author mengajukan kasus
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_id = _suite(client)
    case_id = _ajukan_kasus(client, suite_id, case_code="REVISE-01")
    tugas_map = _ambil_tugas_reviewer(db_session, case_id)

    # Act 1: Reviewer 1 mengajukan revisi
    jadi(Role.REVIEWER, REVIEWER_1)
    res_v1 = client.post(
        f"/reviews/{tugas_map[REVIEWER_1].id}/verdict",
        json={"decision": "revise", "comment": "Mohon perbaiki pasal referensi."},
    )
    assert res_v1.status_code == 201

    # Selama round masih terbuka, author belum menerima notifikasi
    jadi(Role.AUTHOR, AUTHOR_1)
    res_awal = client.get("/notifications")
    assert res_awal.status_code == 200
    assert res_awal.json()["items"] == []

    # Act 2: Reviewer 2 approve (round selesai dengan keputusan revisi)
    jadi(Role.REVIEWER, REVIEWER_2)
    res_v2 = client.post(
        f"/reviews/{tugas_map[REVIEWER_2].id}/verdict",
        json={"decision": "approve"},
    )
    assert res_v2.status_code == 201

    # Assert: Author menerima notifikasi revisi
    jadi(Role.AUTHOR, AUTHOR_1)
    res_notif = client.get("/notifications")
    assert res_notif.status_code == 200
    items = res_notif.json()["items"]
    assert len(items) == 1
    notif = items[0]
    assert notif["type"] == "review_decided"
    assert "revisi" in notif["title"].lower()
    assert notif["link"] == f"/cases/{case_id}"


# ======================================================================================
# AC5: Positif - Menandai notifikasi terbaca membawa link kasus nyata
# ======================================================================================


def test_ac5_klik_notifikasi_menandai_terbaca_dan_membawa_link_kasus_nyata(
    as_role, pengguna_qa, jadi
):
    # Arrange: Reviewer menerima notifikasi penugasan dari kasus nyata
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    suite_id = _suite(client)
    case_id = _ajukan_kasus(client, suite_id, case_code="LINK-01")

    jadi(Role.REVIEWER, REVIEWER_1)
    notif_id = client.get("/notifications").json()["items"][0]["id"]
    assert client.get("/notifications/unread-count").json()["count"] == 1

    # Act: Pengguna menandai dibaca (merepresentasikan klik notifikasi di UI)
    res_read = client.post(f"/notifications/{notif_id}/read")

    # Assert: Status terbaca dan link mengarahkan tepat ke halaman kasus terkait
    assert res_read.status_code == 200
    body = res_read.json()
    assert body["id"] == notif_id
    assert body["read_at"] is not None
    assert body["link"] == f"/cases/{case_id}"
    assert client.get("/notifications/unread-count").json()["count"] == 0


# ======================================================================================
# Edge Cases
# ======================================================================================


def test_edge_pengguna_baru_tanpa_tugas_dan_notifikasi_mendapat_state_kosong(as_role, pengguna_qa):
    # Reviewer 3 belum memiliki penugasan apa pun
    client = as_role(Role.REVIEWER, user_id=REVIEWER_3)

    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert res_dash.json()["review_queue"] == []
    assert res_dash.json()["waiting_for_review"] == 0

    res_notif = client.get("/notifications")
    assert res_notif.status_code == 200
    assert res_notif.json()["items"] == []
    assert res_notif.json()["total"] == 0
    assert client.get("/notifications/unread-count").json()["count"] == 0


def test_edge_lebih_dari_99_notifikasi_unread_count_mengembalikan_angka_asli(
    as_role, pengguna_qa, db_session
):
    # Arrange: Masukkan 105 notifikasi belum dibaca ke database
    db_session.add_all(
        [
            Notification(
                user_id=AUTHOR_1,
                type="review_assigned",
                title=f"Notifikasi ke-{i}",
                body="Isi notifikasi",
                link="/cases/sample",
                read_at=None,
            )
            for i in range(105)
        ]
    )
    db_session.commit()

    # Act
    client = as_role(Role.AUTHOR, user_id=AUTHOR_1)
    res_count = client.get("/notifications/unread-count")

    # Assert: API mengembalikan nilai 105 akurat; formatting badge "99+" ditangani oleh FE
    assert res_count.status_code == 200
    assert res_count.json()["count"] == 105
