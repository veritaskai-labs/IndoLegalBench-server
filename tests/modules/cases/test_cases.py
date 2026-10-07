"""Test endpoint kasus.

PBI-3, SCRUM-106. Membuat, membaca, mengubah, dan mendaftar kasus,
plus kode error yang dikunci tiket.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.cases import repository
from app.modules.cases.models import Case, CaseStatus
from app.modules.cases.router import _user_id
from app.modules.cases.validation import PLACEHOLDER_CASE_CODE_PATTERN
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role, get_current_user
from tests.modules.conftest import USER_ID_QA

AUTHOR_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000b000")
ADMIN_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000c000")


def _suite(client: TestClient, name: str = "Ketenagakerjaan 2026") -> str:
    response = client.post("/suites", json={"name": name, "description": "Tema uji"})
    assert response.status_code == 201
    return response.json()["id"]


def _badan(**ubah) -> dict:
    badan = {
        "case_code": "PHK-001",
        "identity": {
            "title": "PHK sepihak",
            "question": "Apakah PHK tanpa surat sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [
            {
                "regulation_type": "uu",
                "regulation_number": "13",
                "year": 2003,
                "pasal": "151",
            }
        ],
        "answer_criteria": {
            "must_contain": ["surat"],
            "must_not_contain": [],
            "expected_conclusion": "tidak sah",
        },
        "traps": [
            {
                "description": "Mencampur upah dan pesangon",
                "expected_model_behavior": "menolak",
            }
        ],
        "split_tag": "dev",
    }
    badan.update(ubah)
    return badan


def _buat(client: TestClient, suite_id: str, **ubah):
    return client.post(f"/suites/{suite_id}/cases", json=_badan(**ubah))


def _ganti(client: TestClient, user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def test_buat_kasus_draft(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = _buat(client, suite_id)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["case_code"] == "PHK-001"
    assert body["identity"]["title"] == "PHK sepihak"
    assert body["completeness_pct"] == 100
    assert body["version"] == 1
    tersimpan = db_session.get(Case, uuid.UUID(body["id"]))
    assert tersimpan is not None
    assert tersimpan.created_by == USER_ID_QA
    assert tersimpan.status == CaseStatus.DRAFT
    assert tersimpan.completeness["is_complete"] is True
    assert tersimpan.completeness["ready_for_review"] is True
    suite = client.get(f"/suites/{suite_id}").json()
    assert suite["case_count"] == 1
    assert suite["is_empty"] is False
    assert suite["exportable"] is True


def test_detail_kasus(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]

    response = client.get(f"/cases/{case_id}")

    assert response.status_code == 200
    assert response.json()["id"] == case_id
    assert response.json()["legal_refs"][0]["pasal"] == "151"


def test_kasus_tidak_ditemukan(as_role):
    client = as_role(Role.AUTHOR)

    response = client.get(f"/cases/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_suite_tidak_aktif_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200

    response = _buat(client, suite_id)

    assert response.status_code == 422
    assert response.json()["code"] == "SUITE_NOT_ACTIVE"


def test_ubah_suite_tidak_aktif_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200

    response = client.put(f"/cases/{case_id}", json=_badan())

    assert response.status_code == 422
    assert response.json()["code"] == "SUITE_NOT_ACTIVE"


def test_suite_hilang_ditolak(as_role):
    client = as_role(Role.AUTHOR)

    response = _buat(client, str(uuid.uuid4()))

    assert response.status_code == 404


def test_kode_bentrok_menyebut_suite_pemilik(as_role):
    client = as_role(Role.AUTHOR)
    pertama = _suite(client, name="Suite Pemilik")
    kedua = _suite(client, name="Suite Lain")
    assert _buat(client, pertama).status_code == 201

    response = _buat(client, kedua)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CASE_CODE_TAKEN"
    assert "Suite Pemilik" in body["message"]


def test_split_tag_kosong(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    del badan["split_tag"]

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    assert response.json()["code"] == "SPLIT_TAG_REQUIRED"
    assert response.json()["field"] == "split_tag"


def test_pasal_kosong_menyebut_field(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    del badan["legal_refs"][0]["pasal"]

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    assert response.json()["field"] == "legal_refs[0].pasal"
    assert response.json()["code"] == "FIELD_REQUIRED"


def test_judul_kosong(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    badan["identity"]["title"] = "   "

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    assert response.json()["field"] == "identity.title"


def test_pola_case_code_placeholder_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = _buat(client, suite_id, case_code="kode tidak sah")

    assert response.status_code == 422
    assert response.json()["code"] == "CASE_CODE_INVALID"
    assert response.json()["field"] == "case_code"


def test_judul_lebih_dari_300_bukan_wajib(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    badan["identity"]["title"] = "x" * 301

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "300" in body["message"]
    assert "wajib diisi" not in response.text
    assert "required" not in body["message"].lower()


def test_tahun_nol_bukan_wajib(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    badan["legal_refs"][0]["year"] = 0

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["field"] == "legal_refs[0].year"
    assert "wajib diisi" not in response.text


def test_judul_bukan_string_bukan_wajib(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    badan = _badan()
    badan["identity"]["title"] = 123

    response = client.post(f"/suites/{suite_id}/cases", json=badan)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "wajib diisi" not in response.text


def test_draft_boleh_belum_lengkap(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = _buat(
        client,
        suite_id,
        answer_criteria={"must_contain": [], "must_not_contain": []},
        traps=[],
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["completeness_pct"] == 83


def test_daftar_menyaring_tag(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    dev_id = _buat(client, suite_id, case_code="PHK-001").json()["id"]
    test_id = _buat(client, suite_id, case_code="PHK-002", split_tag="test").json()["id"]

    semua = client.get(f"/suites/{suite_id}/cases")
    hanya_test = client.get(f"/suites/{suite_id}/cases", params={"split_tag": "test"})
    draft = client.get(f"/suites/{suite_id}/cases", params={"status": "draft"})
    review = client.get(f"/suites/{suite_id}/cases", params={"status": "in_review"})

    assert semua.status_code == 200
    assert {item["id"] for item in semua.json()} == {dev_id, test_id}
    assert list(semua.json()[0]) == [
        "id",
        "case_code",
        "title",
        "split_tag",
        "status",
        "completeness_pct",
        "updated_at",
    ]
    assert [item["id"] for item in hanya_test.json()] == [test_id]
    assert {item["id"] for item in draft.json()} == {dev_id, test_id}
    assert review.json() == []


def test_daftar_menghitung_ulang_completeness(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    case_id = _buat(client, suite_id, traps=[]).json()["id"]

    kasus = db_session.get(Case, uuid.UUID(case_id))
    assert kasus is not None

    # Simulate a stale stored completeness value.
    kasus.completeness["pct"] = 71
    db_session.commit()

    response = client.get(f"/suites/{suite_id}/cases")

    assert response.status_code == 200
    body = response.json()

    assert body[0]["id"] == case_id
    assert body[0]["completeness_pct"] == 100


def test_pembuat_boleh_mengubah_termasuk_tag(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]

    response = client.put(
        f"/cases/{case_id}",
        json=_badan(
            split_tag="test",
            identity={
                "title": "Judul baru",
                "question": "Pertanyaan baru yang cukup panjang?",
                "category": "ketenagakerjaan",
            },
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["split_tag"] == "test"
    assert body["identity"]["title"] == "Judul baru"
    assert body["status"] == "draft"
    assert body["version"] == 2


def test_author_lain_ditolak(as_role, buat_pengguna):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _ganti(client, buat_pengguna(Role.AUTHOR, user_id=AUTHOR_LAIN))

    response = client.put(f"/cases/{case_id}", json=_badan(case_code="PHK-009"))

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


def test_admin_boleh_mengubah_tapi_bukan_tag_sebelum_approved(as_role, buat_pengguna):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    judul = client.put(
        f"/cases/{case_id}",
        json=_badan(
            identity={
                "title": "Diubah admin",
                "question": "Apakah PHK tanpa surat sah?",
                "category": "ketenagakerjaan",
            }
        ),
    )
    tag = client.put(f"/cases/{case_id}", json=_badan(split_tag="test"))

    assert judul.status_code == 200
    assert judul.json()["identity"]["title"] == "Diubah admin"
    assert judul.json()["split_tag"] == "dev"
    assert tag.status_code == 403
    assert tag.json()["code"] == "SPLIT_TAG_LOCKED"


def test_admin_boleh_mengubah_tag_setelah_approved(as_role, buat_pengguna, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.status = CaseStatus.APPROVED
    db_session.commit()
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    response = client.put(f"/cases/{case_id}", json=_badan(split_tag="test"))

    assert response.status_code == 200
    assert response.json()["split_tag"] == "test"
    assert response.json()["status"] == "approved"


def test_tanpa_sesi_ditolak(client):
    response = client.get(f"/cases/{uuid.uuid4()}")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
def test_peran_lain_ditolak(as_role, role):
    client = as_role(role)

    response = client.get(f"/cases/{uuid.uuid4()}")

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


def test_bentrok_unik_saat_simpan_tetap_case_code_taken(as_role, monkeypatch):
    def bentrok(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception("unique"))

    monkeypatch.setattr(repository, "create", bentrok)
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = _buat(client, suite_id, case_code="PHK-100")

    assert response.status_code == 409
    assert response.json()["code"] == "CASE_CODE_TAKEN"


def test_bentrok_unik_saat_ubah_tetap_case_code_taken(as_role, monkeypatch):
    def bentrok(*_args, **_kwargs):
        raise IntegrityError("UPDATE", {}, Exception("unique"))

    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    monkeypatch.setattr(repository, "save", bentrok)

    response = client.put(f"/cases/{case_id}", json=_badan(case_code="PHK-101"))

    assert response.status_code == 409
    assert response.json()["code"] == "CASE_CODE_TAKEN"


def test_ubah_pola_case_code_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]

    response = client.put(f"/cases/{case_id}", json=_badan(case_code="kode tidak sah"))

    assert response.status_code == 422
    assert response.json()["code"] == "CASE_CODE_INVALID"


def test_filter_status_tidak_dikenal(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    response = client.get(f"/suites/{suite_id}/cases", params={"status": "bukan"})

    assert response.status_code == 422
    assert "detail" in response.json()


def test_suite_berisi_draft_boleh_dihapus(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    assert _buat(client, suite_id).status_code == 201

    assert client.delete(f"/suites/{suite_id}").status_code == 204


def test_suite_berisi_kasus_approved_tidak_bisa_dihapus(as_role, db_session):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.status = CaseStatus.APPROVED
    db_session.commit()

    response = client.delete(f"/suites/{suite_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "SUITE_HAS_APPROVED_CASES"


def test_user_id_dari_string_tetap_uuid():
    class Pengguna:
        user_id = str(USER_ID_QA)

    assert _user_id(Pengguna()) == USER_ID_QA


def test_openapi_memakai_pola_placeholder(client):
    spec = client.get("/openapi.json").json()
    tulis = spec["components"]["schemas"]["CaseWrite"]
    kode = tulis["properties"]["case_code"]

    assert kode["pattern"] == PLACEHOLDER_CASE_CODE_PATTERN
    assert "case_code" in tulis["required"]
    assert "identity" in tulis["required"]
    assert "split_tag" in tulis["required"]
    assert "legal_refs" in tulis["required"]
    assert tulis["properties"]["legal_refs"]["minItems"] == 1
    rujukan = spec["components"]["schemas"]["LegalRef"]
    assert rujukan["required"] == ["regulation_type", "regulation_number", "pasal"]
    assert "/suites/{suite_id}/cases" in spec["paths"]
    assert "/cases/{case_id}" in spec["paths"]


def test_completeness_kasus_lengkap(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]

    response = client.get(f"/cases/{case_id}/completeness")

    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is True
    assert body["ready_for_review"] is True
    assert body["missing"] == []
    assert body["pct"] == 100
    assert body["trap_count"] >= 1
    assert body["legal_ref_count"] >= 1


def test_completeness_tanpa_jebakan_lengkap(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id, traps=[]).json()["id"]

    body = client.get(f"/cases/{case_id}/completeness").json()

    assert body["pct"] == 100
    assert body["is_complete"] is True
    assert body["ready_for_review"] is True
    assert body["missing"] == []
    assert body["trap_count"] == 0


def test_completeness_kasus_tidak_dikenal(as_role):
    client = as_role(Role.AUTHOR)

    response = client.get(f"/cases/{uuid.uuid4()}/completeness")

    assert response.status_code == 404
