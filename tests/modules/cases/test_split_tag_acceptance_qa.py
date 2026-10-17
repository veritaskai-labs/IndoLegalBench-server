"""Uji penerimaan aturan tag dev/test PBI-9. Sub task QA SCRUM-166.

Tes milik SCRUM-164 sudah menguji aturannya satu per satu, dengan kasus yang
disetujui lewat helper. Berkas ini menambah skenario SCRUM-166 yang belum
tercakup, dan kasusnya disetujui lewat alur sungguhan: Author mengajukan,
sistem menugaskan dua reviewer (SCRUM-144), keduanya menyetujui (SCRUM-145).

- AC2: Reviewer tidak bisa mengubah tag lewat jalur mana pun
- AC3: Author tidak bisa mengubah tag kasus yang sudah disetujui
- AC3: setiap perubahan tag oleh Admin tercatat; dev ke test diberi peringatan
- Edge: dev -> test -> dev -> test, penanda "pernah dev" tidak pernah hilang
- AC4: official_cases() setelah tag diubah, sesuai keputusan klien

Skenario lain di SCRUM-166 sudah dijawab tes SCRUM-143/145/164/165; daftarnya
ada di deskripsi PR. Trigger PostgreSQL diuji di
tests/integration/test_split_tag_acceptance_qa_pg.py.
"""

import uuid

import pytest
from fastapi import Depends

from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases import service as cases_service
from app.modules.cases.models import Case
from app.shared import request_context
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import _badan, _buat, _suite
from tests.modules.conftest import USER_ID_QA

REVIEWER_1 = uuid.UUID("00000000-0000-0000-0000-0000000166a1")
REVIEWER_2 = uuid.UUID("00000000-0000-0000-0000-0000000166a2")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000166ad")


@pytest.fixture
def jadi(buat_pengguna):
    """Ganti pengguna dan ikat pelakunya ke session, supaya audit mencatat orang yang benar."""

    def ganti(role: Role, user_id: uuid.UUID) -> None:
        pengguna = buat_pengguna(role, user_id=user_id)

        def pengguna_palsu(db=Depends(get_db)):
            request_context.bind(db, user_id=pengguna.user_id, role=pengguna.role, request_id=None)
            return pengguna

        app.dependency_overrides[get_current_user] = pengguna_palsu

    return ganti


@pytest.fixture
def setujui(db_session, jadi):
    """Setujui kasus lewat alur review sungguhan dan kembali login sebagai Author."""
    db_session.add_all(
        [
            User(
                id=REVIEWER_1, email="r1.166@veritask.test", name="Reviewer 1", role=Role.REVIEWER
            ),
            User(
                id=REVIEWER_2, email="r2.166@veritask.test", name="Reviewer 2", role=Role.REVIEWER
            ),
        ]
    )
    db_session.commit()

    def jalankan(client, case_id: str) -> None:
        diajukan = client.post(f"/cases/{case_id}/submit-review")
        assert diajukan.status_code == 200
        assert diajukan.json()["round_status"] == "open"
        for reviewer in (REVIEWER_1, REVIEWER_2):
            jadi(Role.REVIEWER, reviewer)
            [tugas] = [
                t for t in client.get("/reviews/assigned").json() if t["case"]["case_id"] == case_id
            ]
            nilai = client.post(
                f"/reviews/{tugas['assignment_id']}/verdict", json={"decision": "approve"}
            )
            assert nilai.status_code == 201
        jadi(Role.AUTHOR, USER_ID_QA)
        assert client.get(f"/cases/{case_id}").json()["status"] == "approved"

    return jalankan


def _ubah_tag(client, case_id: str, tag: str, *, confirm: bool = False):
    return client.patch(f"/cases/{case_id}/split-tag", json={"split_tag": tag, "confirm": confirm})


def _riwayat_tag(db_session, case_id: str) -> list[AuditLog]:
    return (
        db_session.query(AuditLog)
        .filter_by(action="case.tag_changed", case_id=uuid.UUID(case_id))
        .order_by(AuditLog.id)
        .all()
    )


# --- AC3: Admin mengubah tag kasus yang disetujui --------------------------------


def test_ac3_positif_tag_kasus_yang_disetujui_lewat_review_diubah_admin(as_role, setujui, jadi):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), split_tag="test").json()["id"]
    setujui(client, case_id)
    jadi(Role.ADMIN, ADMIN)

    # Act
    response = _ubah_tag(client, case_id, "dev")

    # Assert
    assert response.status_code == 200
    assert response.json()["split_tag"] == "dev"
    assert response.json()["status"] == "approved"


def test_ac3_positif_test_ke_dev_tercatat_tanpa_peringatan(as_role, setujui, jadi, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), split_tag="test").json()["id"]
    setujui(client, case_id)
    jadi(Role.ADMIN, ADMIN)

    # Act
    _ubah_tag(client, case_id, "dev")

    # Assert
    [baris] = _riwayat_tag(db_session, case_id)
    assert (baris.before, baris.after) == ({"split_tag": "test"}, {"split_tag": "dev"})
    assert baris.actor_user_id == ADMIN
    assert baris.actor_role == "admin"


def test_edge_bolak_balik_setiap_perubahan_tercatat_dan_pernah_dev_tetap(
    as_role, setujui, jadi, db_session
):
    """dev -> test -> dev -> test: tiga baris audit, peringatan hanya di dev -> test."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), split_tag="dev").json()["id"]
    setujui(client, case_id)
    jadi(Role.ADMIN, ADMIN)

    # Act
    hasil = [
        _ubah_tag(client, case_id, "test", confirm=True),
        _ubah_tag(client, case_id, "dev"),
        _ubah_tag(client, case_id, "test", confirm=True),
    ]

    # Assert
    assert [r.status_code for r in hasil] == [200, 200, 200]
    assert [r.json()["was_dev"] for r in hasil] == [True, True, True]
    assert [baris.after for baris in _riwayat_tag(db_session, case_id)] == [
        {"split_tag": "test", "warning": True},
        {"split_tag": "dev"},
        {"split_tag": "test", "warning": True},
    ]


def test_edge_konfirmasi_ditolak_tidak_meninggalkan_jejak(as_role, setujui, jadi, db_session):
    """Sisi server dari "Admin membatalkan dialog": tanpa confirm tidak ada yang berubah."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), split_tag="dev").json()["id"]
    setujui(client, case_id)
    jadi(Role.ADMIN, ADMIN)

    # Act
    response = _ubah_tag(client, case_id, "test")

    # Assert
    assert response.status_code == 409
    assert response.json()["requires_confirmation"] is True
    kasus = client.get(f"/cases/{case_id}").json()
    assert (kasus["split_tag"], kasus["was_dev"]) == ("dev", False)
    assert _riwayat_tag(db_session, case_id) == []


def test_edge_tag_diubah_saat_v2_direview_lalu_v2_disetujui(as_role, setujui, jadi):
    """Usulan Roben di #57: perubahan tag ikut ke versi terbuka (#53) supaya menyetujui
    v2 tidak membawa kembali tag lama. Di sini v2 benar-benar disetujui setelahnya."""
    # Arrange: v1 dev disetujui, v2 diajukan
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client), split_tag="dev").json()["id"]
    setujui(client, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    jadi(Role.ADMIN, ADMIN)

    # Act: tag berubah di tengah review, lalu kedua reviewer menyetujui v2
    assert _ubah_tag(client, case_id, "test", confirm=True).status_code == 200
    for reviewer in (REVIEWER_1, REVIEWER_2):
        jadi(Role.REVIEWER, reviewer)
        [tugas] = [
            t for t in client.get("/reviews/assigned").json() if t["case"]["case_id"] == case_id
        ]
        assert tugas["case"]["version_no"] == 2
        assert tugas["case"]["split_tag"] == "test"  # AC2: reviewer melihat tag terbaru
        nilai = client.post(
            f"/reviews/{tugas['assignment_id']}/verdict", json={"decision": "approve"}
        )
        assert nilai.status_code == 201
    jadi(Role.AUTHOR, USER_ID_QA)

    # Assert
    kasus = client.get(f"/cases/{case_id}").json()
    assert (kasus["version"], kasus["status"], kasus["split_tag"], kasus["was_dev"]) == (
        2,
        "approved",
        "test",
        True,
    )


# --- AC3: Author tidak boleh mengubah tag setelah disetujui ------------------------


def test_ac3_negatif_author_tidak_bisa_memakai_jalur_admin(as_role, setujui):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    setujui(client, case_id)

    # Act
    response = _ubah_tag(client, case_id, "test", confirm=True)

    # Assert
    assert response.status_code == 403
    assert client.get(f"/cases/{case_id}").json()["split_tag"] == "dev"


def test_ac3_negatif_author_tidak_bisa_mengubah_tag_di_draf_versi_berikutnya(as_role, setujui):
    """SCRUM-166 menulis 403. Lewat PUT jawabannya 409 SPLIT_TAG_LOCKED, keputusan di
    review client #30: biasanya tag baru saja diubah Admin, dan 403 membuat client
    pindah ke /forbidden sehingga suntingan hilang."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    setujui(client, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201

    # Act
    response = client.put(f"/cases/{case_id}", json=_badan(split_tag="test"))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "SPLIT_TAG_LOCKED"
    assert client.get(f"/cases/{case_id}/versions/current").json()["split_tag"] == "dev"


# --- AC2: Reviewer tidak mengubah tag -------------------------------------------


def test_ac2_negatif_reviewer_tidak_bisa_mengubah_tag_lewat_put(as_role, jadi):
    """Jalur PUT ditolak oleh guard peran (require_roles AUTHOR, ADMIN), bukan oleh
    aturan tag. Tetap dipertahankan supaya AC2 "tidak lewat jalur mana pun" tercakup."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    jadi(Role.REVIEWER, REVIEWER_1)

    # Act
    response = client.put(f"/cases/{case_id}", json=_badan(split_tag="test"))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.get(f"/cases/{case_id}").json()["split_tag"] == "dev"


# --- AC4: skor resmi -------------------------------------------------------------


def test_ac4_positif_kasus_yang_dipindah_ke_test_ikut_skor_resmi(
    as_role, setujui, jadi, db_session
):
    """Keputusan klien (review #53): kasus dev yang dipindah ke test tetap dihitung."""
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dipindah = _buat(client, suite_id, case_code="QA-166-PINDAH", split_tag="dev").json()["id"]
    tetap_dev = _buat(client, suite_id, case_code="QA-166-DEV", split_tag="dev").json()["id"]
    sedang_review = _buat(client, suite_id, case_code="QA-166-REVIEW", split_tag="test").json()[
        "id"
    ]
    setujui(client, dipindah)
    setujui(client, tetap_dev)
    assert client.post(f"/cases/{sedang_review}/submit-review").status_code == 200
    jadi(Role.ADMIN, ADMIN)
    assert _ubah_tag(client, dipindah, "test", confirm=True).status_code == 200

    # Act
    resmi = cases_service.official_cases(db_session, uuid.UUID(suite_id))

    # Assert
    assert [salinan.case_code for salinan in resmi] == ["QA-166-PINDAH"]
    assert db_session.get(Case, uuid.UUID(dipindah)).was_dev is True
