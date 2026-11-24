"""Bacaan kasus dengan asal dan tag kosong, serta sidik jari saat kasus ditulis.
PBI-4 SCRUM-81 SCRUM-337, AC2, AC8, diagram D8 dan D9.

Draft hasil impor dan duplikasi boleh tanpa tag dev/test. Di sini dibuktikan bahwa draft
seperti itu tetap bisa dibaca di detail, daftar, dan perbandingan versi; tidak pernah lolos
pengajuan review; dan tertutup untuk Developer AI. Draft tanpa tag dibuat langsung lewat ORM,
karena endpoint yang membuatnya (impor dan duplikasi) baru ada di chunk berikutnya.

Label: Positif = jalur normal, Negatif = harus ditolak atau disembunyikan, Edge = data batas.
"""

import uuid

import pytest

from app.modules.cases.fingerprint import content_fingerprint
from app.modules.cases.models import Case, CaseOrigin, ImportBatch
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _ganti, _suite


@pytest.fixture
def penulis(as_role):
    return as_role(Role.CONTRIBUTOR)


def _kasus_tanpa_tag(client, db_session) -> tuple[str, str, Case]:
    """Satu draft yang tagnya dikosongkan, seperti hasil impor atau duplikasi."""
    suite_id = _suite(client)
    dibuat = _buat(client, suite_id).json()
    kasus = db_session.get(Case, uuid.UUID(dibuat["id"]))
    kasus.current_version.split_tag = None
    db_session.commit()
    return suite_id, dibuat["id"], kasus


def test_kasus_manual_menampilkan_asal_manual(penulis):
    """Positif: D8, kasus yang ditulis lewat form tampil sebagai manual tanpa rujukan asal."""
    # Arrange
    dibuat = _buat(penulis, _suite(penulis)).json()

    # Act
    detail = penulis.get(f"/cases/{dibuat['id']}").json()

    # Assert
    assert (dibuat["origin"], dibuat["source_case_id"], dibuat["import_batch_id"]) == (
        "manual",
        None,
        None,
    )
    assert detail["origin"] == "manual"


def test_draft_tanpa_tag_tetap_bisa_dibaca_di_detail_daftar_dan_perbandingan(penulis, db_session):
    """Positif: AC8, draft tanpa tag dibaca dengan split_tag null, tidak menyebabkan galat."""
    # Arrange
    suite_id, case_id, _ = _kasus_tanpa_tag(penulis, db_session)

    # Act
    detail = penulis.get(f"/cases/{case_id}")
    daftar = penulis.get(f"/suites/{suite_id}/cases")
    banding = penulis.get(f"/cases/{case_id}/versions/compare", params={"a": 1, "b": 1})

    # Assert
    assert (detail.status_code, detail.json()["split_tag"]) == (200, None)
    assert [item["split_tag"] for item in daftar.json()] == [None]
    assert (banding.status_code, banding.json()["a"]["sections"]["split_tag"]) == (200, None)


def test_filter_tag_tidak_memuat_draft_tanpa_tag(penulis, db_session):
    """Edge: AC8, selama tag kosong kasus tidak dihitung dev maupun test (D8)."""
    # Arrange
    suite_id, _, _ = _kasus_tanpa_tag(penulis, db_session)

    # Act
    per_tag = {
        tag: penulis.get(f"/suites/{suite_id}/cases", params={"split_tag": tag}).json()
        for tag in ("dev", "test")
    }

    # Assert
    assert per_tag == {"dev": [], "test": []}


def test_draft_tanpa_tag_tidak_bisa_diajukan_review(penulis, db_session):
    """Negatif: keputusan SA D7 dan D8, tag wajib dipilih sebelum kasus diajukan review."""
    # Arrange
    _, case_id, _ = _kasus_tanpa_tag(penulis, db_session)

    # Act
    respons = penulis.post(f"/cases/{case_id}/submit-review")

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (422, "CASE_NOT_READY")
    assert "split_tag" in {kurang["field"] for kurang in respons.json()["missing"]}


def test_draft_tanpa_tag_tertutup_untuk_developer_ai(penulis, db_session, buat_pengguna):
    """Negatif: D9 aturan 4, kasus tanpa tag diperlakukan tertutup seperti kasus test."""
    # Arrange
    suite_id, case_id, _ = _kasus_tanpa_tag(penulis, db_session)
    _ganti(penulis, buat_pengguna(Role.DEVELOPER_AI, user_id=uuid.uuid4()))

    # Act
    detail = penulis.get(f"/cases/{case_id}")
    daftar = penulis.get(f"/suites/{suite_id}/cases")

    # Assert
    assert detail.status_code == 404
    assert daftar.json() == []


def test_kasus_impor_menampilkan_batch_asalnya(penulis, db_session):
    """Positif: D8, kasus hasil impor menunjuk batch impornya di detail dan daftar."""
    # Arrange
    suite_id, case_id, kasus = _kasus_tanpa_tag(penulis, db_session)
    batch = ImportBatch(file_name="lama.csv", imported_by=kasus.created_by, suite_id=kasus.suite_id)
    db_session.add(batch)
    db_session.flush()
    kasus.origin, kasus.import_batch_id = CaseOrigin.IMPORT, batch.id
    db_session.commit()

    # Act
    detail = penulis.get(f"/cases/{case_id}").json()
    (baris,) = penulis.get(f"/suites/{suite_id}/cases").json()

    # Assert
    assert (detail["origin"], detail["import_batch_id"]) == ("import", str(batch.id))
    assert baris["origin"] == "import"


def test_sidik_jari_diisi_saat_dibuat_dan_ikut_berubah_saat_judul_diubah(penulis, db_session):
    """Positif: D7, sidik jari mengikuti judul dan pertanyaan versi yang sedang dikerjakan,
    jadi impor berikutnya mengenali kasus ini dengan isi terbarunya."""
    # Arrange
    dibuat = _buat(penulis, _suite(penulis)).json()
    kasus = db_session.get(Case, uuid.UUID(dibuat["id"]))
    awal = kasus.content_fingerprint
    identitas = {**_badan()["identity"], "title": "PHK massal"}

    # Act
    respons = penulis.put(f"/cases/{dibuat['id']}", json=_badan(identity=identitas))
    db_session.refresh(kasus)

    # Assert
    assert respons.status_code == 200, respons.text
    assert awal == content_fingerprint(_badan()["identity"]["title"], identitas["question"])
    assert kasus.content_fingerprint == content_fingerprint("PHK massal", identitas["question"])
