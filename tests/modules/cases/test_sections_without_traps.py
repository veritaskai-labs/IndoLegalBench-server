"""Bagian traps keluar dari compare versi dan body snapshot baru (SCRUM-256).

Versi dan snapshot lama tetap menyimpan traps di JSON-nya. Yang berubah hanya
apa yang dibaca dan dibandingkan: traps tidak lagi menjadi bagian kasus.
"""

import uuid

from app.modules.cases import sections, service
from app.modules.cases.models import Case
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite

TRAPS_LAMA = [{"description": "Mencampur upah dan pesangon", "expected_model_behavior": "menolak"}]
TUJUH_BAGIAN = [
    "identity.title",
    "identity.question",
    "category",
    "case_code",
    "split_tag",
    "legal_refs",
    "answer_criteria",
]


def _isi(**tambahan):
    return {
        "title": "PHK",
        "question": "Sah?",
        "category": None,
        "answer_criteria": {"must_contain": ["surat"]},
        **tambahan,
    }


def _tanam_traps_lama(db_session, case_id: str, traps: list) -> None:
    kasus = db_session.get(Case, uuid.UUID(case_id))
    versi = kasus.current_version
    versi.content = {**versi.content, "traps": traps}
    db_session.commit()


def test_bagian_kasus_tinggal_tujuh_tanpa_traps():
    assert list(sections.SECTIONS) == TUJUH_BAGIAN


def test_sections_from_tidak_menyalin_traps_dari_versi_lama():
    # Arrange
    isi = _isi(traps=TRAPS_LAMA)

    # Act
    hasil = sections.sections_from(case_code="PHK-001", split_tag="dev", content=isi, legal_refs=[])

    # Assert
    assert "traps" not in hasil
    assert list(hasil) == TUJUH_BAGIAN
    assert isi["traps"] == TRAPS_LAMA


def test_beda_traps_saja_bukan_perubahan_bagian():
    # Arrange
    sebelum = sections.sections_from(
        case_code="PHK-001", split_tag="dev", content=_isi(traps=TRAPS_LAMA), legal_refs=[]
    )
    sesudah = sections.sections_from(
        case_code="PHK-001", split_tag="dev", content=_isi(traps=[]), legal_refs=[]
    )

    # Act
    berubah = sections.changed_sections(sebelum, sesudah)

    # Assert
    assert berubah == []


def test_body_snapshot_baru_tidak_memuat_traps():
    # Act
    body = sections.snapshot_body(
        case_code="PHK-001",
        version_no=1,
        status="approved",
        split_tag="dev",
        content=_isi(traps=TRAPS_LAMA),
        legal_refs=[],
    )

    # Assert
    assert "traps" not in body["sections"]
    assert body["version_no"] == 1


def test_salinan_approved_suite_tidak_memuat_traps_dan_versi_tetap_utuh(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _buat(client, suite_id).json()["id"]
    _tanam_traps_lama(db_session, case_id, TRAPS_LAMA)
    _setujui(db_session, case_id)

    # Act
    salinan = service.approved_copies_for_suite(db_session, uuid.UUID(suite_id))

    # Assert
    assert len(salinan) == 1
    assert "traps" not in salinan[0]["body"]["sections"]
    kasus = db_session.get(Case, uuid.UUID(case_id))
    assert kasus.current_version.content["traps"] == TRAPS_LAMA


def test_bandingkan_versi_mengabaikan_traps_lama(as_role, db_session):
    # Arrange: v1 approved dengan traps lama, v2 draf dengan traps lain dan judul baru
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _tanam_traps_lama(db_session, case_id, TRAPS_LAMA)
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    _tanam_traps_lama(db_session, case_id, [{"description": "Jebakan lain"}])
    client.put(
        f"/cases/{case_id}",
        json=_badan(identity={**_badan()["identity"], "title": "Judul baru"}),
    )

    # Act
    respons = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2})

    # Assert
    assert respons.status_code == 200
    badan = respons.json()
    assert badan["changed"] == ["identity.title"]
    assert "traps" not in badan["a"]["sections"]
    assert "traps" not in badan["b"]["sections"]


def test_bandingkan_versi_yang_hanya_beda_traps_tidak_menandai_perubahan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _tanam_traps_lama(db_session, case_id, TRAPS_LAMA)
    _setujui(db_session, case_id)
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    _tanam_traps_lama(db_session, case_id, [])

    # Act
    respons = client.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 2})

    # Assert
    assert respons.status_code == 200
    assert respons.json()["changed"] == []
