"""Uji penerimaan jejak audit PBI-18. Sub task QA SCRUM-142.

Tes milik SCRUM-140 sudah menguji modul audit dari dalam. Berkas ini
hanya menambah skenario SCRUM-142 yang belum tercakup, dan semuanya
dijalankan lewat HTTP seperti pengguna sungguhan:

- AC1: snapshot suite (PBI-8) dan versi baru hasil fork tercatat tepat satu kali
- AC3: saringan pelaku yang akunnya sudah dinonaktifkan tetap menemukan catatannya
- AC5: Reviewer melihat riwayat kasus yang pernah ditugaskan (SCRUM-167)
- AC6: nilai kredensial tidak ikut ke file CSV maupun PDF
- Edge: aksi yang ditolak validasi (422) atau bentrok (409) tidak meninggalkan catatan

Skenario lain di SCRUM-142 sudah dijawab tes SCRUM-140, daftarnya ada di
deskripsi PR. AC2 dan AC7 di PostgreSQL ada di
tests/integration/test_audit_acceptance_qa_pg.py.
"""

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends

from app.main import app
from app.modules.audit import export
from app.modules.audit.export import ExportFormat, PdfExporter
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.reviews.models import AssignmentStatus, ReviewAssignment, ReviewRound
from app.shared import request_context
from app.shared.config import get_settings
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import _buat, _setujui, _suite
from tests.modules.conftest import USER_ID_QA

PENULIS_LAMA = uuid.UUID("00000000-0000-0000-0000-00000000a142")
REVIEWER_QA = uuid.UUID("00000000-0000-0000-0000-00000000a167")
_RAHASIA = "qa-provider-token-4wLz"  # pragma: allowlist secret
_RAHASIA_BARU = "qa-replacement-token-8nTe"  # pragma: allowlist secret


@pytest.fixture
def encryption_key(monkeypatch):
    """Kunci Fernet khusus test. Kredensial produk AI wajib terenkripsi (PBI-10 AC2)."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _catatan(db_session, action: str) -> list[AuditLog]:
    return db_session.query(AuditLog).filter(AuditLog.action == action).all()


def _jumlah_catatan(db_session) -> int:
    return db_session.query(AuditLog).count()


def _jadi(client, buat_pengguna, role: Role, **identitas) -> None:
    """Ganti pengguna yang login tanpa membuat TestClient baru.

    Pelaku diikat ke session seperti get_current_user asli. Tanpa bind ini
    listener audit memakai pelaku request sebelumnya, karena semua request
    test berbagi satu db_session.
    """
    pengguna = buat_pengguna(role, **identitas)

    def pengguna_palsu(db=Depends(get_db)):
        request_context.bind(db, user_id=pengguna.user_id, role=pengguna.role, request_id=None)
        return pengguna

    app.dependency_overrides[get_current_user] = pengguna_palsu


# --- AC1: event katalog D6 tercatat tepat satu kali ---------------------------


def test_ac1_positif_snapshot_suite_tercatat_sekali(as_role, buat_pengguna, db_session):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    _jadi(client, buat_pengguna, Role.ADMIN)

    response = client.post(f"/suites/{suite_id}/snapshots")

    assert response.status_code == 201
    catatan = _catatan(db_session, "suite.snapshot_created")
    assert len(catatan) == 1
    baris = catatan[0]
    assert baris.entity_type == "suite"
    assert baris.entity_id == uuid.UUID(suite_id)
    assert baris.actor_user_id == USER_ID_QA
    assert baris.after == {"snapshot_id": response.json()["id"], "case_count": 1}


def test_ac1_positif_versi_baru_hasil_fork_tercatat_sekali(as_role, db_session):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _setujui(db_session, case_id)
    sebelum = len(_catatan(db_session, "case.version_created"))

    response = client.post(f"/cases/{case_id}/versions")

    assert response.status_code == 201
    versi_baru = _catatan(db_session, "case.version_created")[sebelum:]
    assert len(versi_baru) == 1
    assert versi_baru[0].after == {"version_no": 2}
    assert versi_baru[0].case_id == uuid.UUID(case_id)
    assert versi_baru[0].actor_user_id == USER_ID_QA


# --- AC3: pengguna yang sudah dinonaktifkan ---------------------------------


def test_ac3_edge_pelaku_yang_dinonaktifkan_tetap_bisa_dicari(as_role, buat_pengguna, db_session):
    db_session.add(
        User(
            id=PENULIS_LAMA, email="penulis.lama@veritask.test", name="Penulis Lama", role="author"
        )
    )
    db_session.add(
        User(id=USER_ID_QA, email="admin.qa@veritask.test", name="Admin QA", role="admin")
    )
    db_session.commit()
    client = as_role(Role.CONTRIBUTOR, user_id=PENULIS_LAMA, name="Penulis Lama")
    _suite(client, name="Suite Penulis Lama")
    _jadi(client, buat_pengguna, Role.ADMIN, name="Admin QA")
    assert client.post(f"/admin/users/{PENULIS_LAMA}/deactivate").status_code == 200

    per_id = client.get("/audit-logs", params={"actor_id": str(PENULIS_LAMA)}).json()
    per_nama = client.get("/audit-logs", params={"actor": "penulis lama"}).json()

    assert db_session.get(User, PENULIS_LAMA).is_active is False
    for hasil in (per_id, per_nama):
        assert [item["action"] for item in hasil["items"]] == ["suite.created"]
        assert hasil["items"][0]["actor_name"] == "Penulis Lama"
        assert hasil["items"][0]["actor_email"] == "penulis.lama@veritask.test"


# --- AC5: Reviewer --------------------------------------------------------------


@pytest.mark.parametrize("status", [AssignmentStatus.ACTIVE, AssignmentStatus.REPLACED])
def test_ac5_positif_reviewer_melihat_riwayat_kasus_yang_ditugaskan(as_role, db_session, status):
    """SCRUM-167. Reviewer yang sudah diganti Admin tetap melihat riwayatnya (asumsi #10).

    Bagian negatif AC5 sudah diuji di test_audit_api.py. Assignment disisipkan
    langsung karena penugasan otomatis (SCRUM-144) belum masuk staging.
    """
    # Arrange
    penulis = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(penulis)
    ditugaskan = _buat(penulis, suite_id).json()["id"]
    lain = _buat(penulis, suite_id, case_code="PHK-002").json()["id"]
    assert penulis.post(f"/cases/{ditugaskan}/submit-review").status_code == 200
    ronde = db_session.query(ReviewRound).filter_by(case_id=uuid.UUID(ditugaskan)).one()
    db_session.add(
        User(id=REVIEWER_QA, email="reviewer.qa@veritask.ai", name="Reviewer QA", role="reviewer")
    )
    db_session.add(
        ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER_QA, status=status)
    )
    db_session.commit()
    reviewer = as_role(Role.CONTRIBUTOR, user_id=REVIEWER_QA)

    # Act
    daftar = reviewer.get("/audit-logs")
    detail = reviewer.get(f"/audit-logs/{daftar.json()['items'][0]['id']}")
    kasus_lain = reviewer.get("/audit-logs", params={"case_id": lain})

    # Assert
    assert daftar.status_code == 200
    assert daftar.json()["total"] > 0
    assert {item["case_id"] for item in daftar.json()["items"]} == {ditugaskan}
    assert detail.status_code == 200
    assert kasus_lain.status_code == 403


# --- AC6: kredensial tidak ikut ke file unduhan -------------------------------


@pytest.mark.parametrize("format_", ["csv", "pdf"])
def test_ac6_negatif_kredensial_tidak_ada_di_file_unduhan(
    as_role, monkeypatch, encryption_key, format_
):
    # PDF bawaan dikompres, jadi teks rahasia tidak akan terbaca walau ada.
    # Pembuat PDF diganti versi tanpa kompresi supaya pencarian byte bermakna.
    monkeypatch.setitem(export._EXPORTERS, ExportFormat.PDF, PdfExporter(compress=False))
    client = as_role(Role.ADMIN)
    produk = client.post(
        "/admin/providers",
        json={
            "name": "Produk QA",
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.test/v1",
            "model_name": "qa-1",
            "credential": _RAHASIA,
            "rate_limit_per_minute": 30,
            "monthly_budget_idr": "1500000.00",
        },
    )
    assert produk.status_code == 201
    ganti = client.patch(
        f"/admin/providers/{produk.json()['id']}", json={"credential": _RAHASIA_BARU}
    )
    assert ganti.status_code == 200

    response = client.get(
        "/audit-logs/export", params={"format": format_, "entity_type": "ai_product"}
    )

    assert response.status_code == 200
    isi = response.content
    # Baris rotasi memang ada di file; tanpa ini test bisa lulus karena file kosong.
    # Dicek lewat penanda "rotated", karena kolom aksi PDF sempit dan memotong
    # "ai_product.credential_rotated" ke baris berikutnya.
    assert b"rotated" in isi
    assert _RAHASIA.encode() not in isi
    assert _RAHASIA_BARU.encode() not in isi


# --- Edge: aksi yang gagal tidak meninggalkan catatan --------------------------


def test_edge_aksi_ditolak_validasi_atau_bentrok_tidak_tercatat(as_role, db_session):
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    _buat(client, suite_id)
    sebelum = _jumlah_catatan(db_session)

    ditolak = [
        client.post("/suites", json={"name": ""}),
        client.post("/suites", json={"name": "Ketenagakerjaan 2026"}),
        client.post(f"/suites/{suite_id}/cases", json={"case_code": "PHK-002"}),
        _buat(client, suite_id),
    ]

    assert [r.status_code for r in ditolak] == [422, 409, 422, 409]
    assert _jumlah_catatan(db_session) == sebelum
