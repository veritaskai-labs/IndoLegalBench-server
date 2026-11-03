"""Isi snapshot otomatis dengan kasus sungguhan di database. SCRUM-76 SCRUM-207.

PBI-11 AC2 dan AC6: hanya kasus yang disetujui yang dibekukan, dan kasus
yang sedang diedit memakai versi disetujui terbarunya, bukan drafnya. Perilaku
ini dulu diuji lewat POST /suites/{id}/snapshots (SCRUM-137); endpoint itu
dihapus di AC7, jadi pengujiannya pindah ke sumber snapshot otomatis.

Label: Positif = kasus ikut dibekukan, Negatif = kasus tidak ikut, Edge = batas perilaku.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.modules.cases.models import Case
from app.modules.runs import adapters
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite

JUDUL_V2 = "PHK sepihak tanpa surat peringatan"


def _versi_berlaku(db_session, case_id: str) -> uuid.UUID:
    db_session.expire_all()
    return db_session.get(Case, uuid.UUID(case_id)).latest_approved_version_id


def _mulai_v2(client, case_id: str) -> None:
    """Buka versi 2 sebagai draf dengan judul baru, seperti Kontributor yang sedang mengedit."""
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    revisi = _badan()
    revisi["identity"] = {**revisi["identity"], "title": JUDUL_V2}
    assert client.put(f"/cases/{case_id}", json=revisi).status_code == 200


@pytest.fixture
def suite(as_role, db_session):
    """Satu kasus approved (v1) dan satu kasus yang belum pernah disetujui."""
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    disetujui = _buat(client, suite_id).json()["id"]
    draf = _buat(client, suite_id, case_code="PHK-002").json()["id"]
    _setujui(db_session, disetujui)
    return SimpleNamespace(
        client=client,
        id=uuid.UUID(suite_id),
        disetujui=disetujui,
        draf=draf,
        v1=_versi_berlaku(db_session, disetujui),
    )


def _bekukan(db_session, suite_id: uuid.UUID):
    return adapters.SnapshotSourceAdapter(db_session).approved_items((suite_id,))


def test_hanya_kasus_yang_disetujui_dibekukan(suite, db_session):
    """Positif dan negatif: kasus approved ikut, kasus yang hanya punya draf tidak (AC2)."""
    # Act
    items = _bekukan(db_session, suite.id)

    # Assert
    assert [str(item.case_id) for item in items] == [suite.disetujui]
    assert suite.draf not in {str(item.case_id) for item in items}


def test_isi_snapshot_adalah_salinan_versi_yang_disetujui(suite, db_session):
    """Positif: AC6, isi persis versi approved dengan kode dan tag kasus."""
    # Act
    (item,) = _bekukan(db_session, suite.id)

    # Assert
    assert item.case_version_id == suite.v1
    assert item.split_tag == "dev"
    assert item.content["version_no"] == 1
    assert item.content["status"] == "approved"
    assert item.content["sections"]["case_code"] == "PHK-001"


def test_kasus_yang_sedang_diedit_memakai_versi_disetujui_terbaru(suite, db_session):
    """Edge: AC2, draf v2 yang sedang diedit tidak ikut; snapshot tetap memegang v1."""
    # Arrange
    _mulai_v2(suite.client, suite.disetujui)

    # Act
    (item,) = _bekukan(db_session, suite.id)

    # Assert
    assert item.case_version_id == suite.v1
    assert item.content["sections"]["identity.title"] != JUDUL_V2


def test_setelah_v2_disetujui_snapshot_berikutnya_memegang_v2(suite, db_session):
    """Positif: snapshot diambil saat sesi berjalan, jadi persetujuan terbaru ikut terbawa."""
    # Arrange
    _mulai_v2(suite.client, suite.disetujui)
    _setujui(db_session, suite.disetujui)

    # Act
    (item,) = _bekukan(db_session, suite.id)

    # Assert
    assert item.case_version_id == _versi_berlaku(db_session, suite.disetujui)
    assert item.case_version_id != suite.v1
    assert item.content["sections"]["identity.title"] == JUDUL_V2


def test_suite_tanpa_kasus_disetujui_tidak_membekukan_apa_pun(as_role, db_session):
    """Edge: suite kosong menghasilkan snapshot kosong, bukan galat."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = uuid.UUID(_suite(client, "Suite kosong"))

    # Act + Assert
    assert _bekukan(db_session, suite_id) == []
