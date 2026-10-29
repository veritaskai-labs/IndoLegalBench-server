"""Rujukan hukum di riwayat, perbandingan versi, dan snapshot suite (PBI-21, SCRUM-160).

Semua bacaan lain sudah mengembalikan bentuk PBI-21 dengan display_text. Bagian
ini menutup sisanya, supaya client (SCRUM-161) tidak perlu menulis rujukan
sendiri: compare dan snapshot memakai pembaca yang sama, dan rujukan Sprint 1
yang disimpan ulang dalam bentuk baru tidak dihitung sebagai perubahan.

Contoh hukum hanya KTK-001 dan contoh AC10 (belum dikonfirmasi tim legal).
"""

import uuid

from app.modules.auth.models import User
from app.modules.cases.models import Case
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.modules.cases.test_cases import ADMIN_LAIN, _badan, _buat, _ganti, _setujui, _suite
from tests.modules.conftest import USER_ID_QA

TEKS_KTK_001 = "Pasal 156 UU 13/2003"
TEKS_AC10 = "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"
LAMA_KTK_001 = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}


def _pengguna(db_session) -> None:
    db_session.add_all(
        [
            User(
                id=USER_ID_QA,
                name="Pengguna QA",
                email="qa-160h@veritask.test",
                role=Role.CONTRIBUTOR,
            ),
            User(id=ADMIN_LAIN, name="Admin QA", email="admin-160h@veritask.test", role=Role.ADMIN),
        ]
    )
    db_session.commit()


def _kasus_sprint_1_approved(client, db_session) -> tuple[str, str]:
    """Versi 1 approved yang content-nya masih bentuk Sprint 1 (KTK-001)."""
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    versi.content = {**versi.content, "legal_refs": [LAMA_KTK_001]}
    db_session.commit()
    _setujui(db_session, case_id)
    return suite_id, case_id


def _versi_2_judul_baru(client, case_id: str, *rujukan: dict) -> None:
    """Versi 2: judul diubah, rujukan disimpan editor baru."""
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    badan = _badan(legal_refs=list(rujukan))
    badan["identity"] = {**badan["identity"], "title": "Judul versi 2"}
    assert client.put(f"/cases/{case_id}", json=badan).status_code == 200


def test_compare_versi_sprint_1_dengan_versi_baru_berisi_display_text(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _pengguna(db_session)
    _, case_id = _kasus_sprint_1_approved(client, db_session)
    _versi_2_judul_baru(client, case_id, rujukan_ktk_001())

    # Act
    body = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2}).json()

    # Assert
    for sisi in ("a", "b"):
        rujukan = body[sisi]["sections"]["legal_refs"]
        assert [item["display_text"] for item in rujukan] == [TEKS_KTK_001]
        assert rujukan[0]["kind"] == "structured"
        assert rujukan[0]["links"][0]["regulation_type"] == "uu"


def test_rujukan_sprint_1_yang_disimpan_ulang_tidak_dihitung_berubah(as_role, db_session):
    """Bentuk penyimpanannya berbeda, isinya sama: yang berubah hanya judul."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _pengguna(db_session)
    _, case_id = _kasus_sprint_1_approved(client, db_session)
    _versi_2_judul_baru(client, case_id, rujukan_ktk_001())

    # Act
    compare = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2}).json()
    riwayat = client.get(f"/cases/{case_id}/versions").json()

    # Assert
    assert compare["changed"] == ["identity.title"]
    assert riwayat[1]["changed"] == ["identity.title"]


def test_rujukan_yang_benar_benar_berubah_tetap_tercatat(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _pengguna(db_session)
    _, case_id = _kasus_sprint_1_approved(client, db_session)
    _versi_2_judul_baru(client, case_id, rujukan_ac10())

    # Act
    body = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2}).json()

    # Assert
    assert body["changed"] == ["identity.title", "legal_refs"]
    assert body["b"]["sections"]["legal_refs"][0]["display_text"] == TEKS_AC10


def test_snapshot_suite_menyimpan_rujukan_dalam_bentuk_baru(as_role, buat_pengguna, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    _pengguna(db_session)
    suite_id, _ = _kasus_sprint_1_approved(client, db_session)
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN, name="Admin QA"))

    # Act
    dibuat = client.post(f"/suites/{suite_id}/snapshots")

    # Assert
    assert dibuat.status_code == 201, dibuat.text
    rujukan = dibuat.json()["items"][0]["body"]["sections"]["legal_refs"]
    assert [item["display_text"] for item in rujukan] == [TEKS_KTK_001]
    assert rujukan[0]["links"][0]["number"] == "13"


def test_kontrak_compare_memakai_rujukan_baca(client):
    # Arrange
    spec = client.get("/openapi.json").json()
    # Act
    properti = spec["components"]["schemas"]["VersionSections"]["properties"]["legal_refs"]

    # Assert
    assert properti["items"]["$ref"].endswith("/LegalRefRead")
