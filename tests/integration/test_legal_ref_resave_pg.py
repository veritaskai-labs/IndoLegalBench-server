"""SCRUM-163: kasus Sprint 1 disimpan ulang di PostgreSQL (PBI-21 AC9).

Meniru kasus di dev: versi approved dengan content bentuk Sprint 1 (jenis "UU"
seperti sebelum PBI-21), lalu Author membuka versi baru dan menekan Simpan
dengan rujukan hasil bacaan server, tanpa mengetik ulang. Yang dibuktikan di
PostgreSQL: baris link versi draf ditulis ulang di bawah foreign key dan check
constraint SCRUM-159, sementara content dan baris versi approved tidak berubah.

Contoh hukum hanya KTK-001.
"""

from app.modules.cases import service
from app.modules.cases.models import Case
from tests.integration.test_legal_refs_dual_write_pg import (  # noqa: F401 - fixture db
    KTK_001,
    _badan,
    _baris,
    _buat,
    _setujui,
    db,
    pytestmark,
)
from tests.legal_refs import rujukan_ktk_001

LAMA_KTK_001 = {"regulation_type": "UU", "regulation_number": "13", "year": 2003, "pasal": "156"}


def test_ac9_ktk_001_sprint_1_disimpan_ulang_tanpa_menyentuh_versi_approved(db):  # noqa: F811
    # Arrange
    sesi, suite_id, user_id = db
    _, kasus = _buat(sesi, suite_id, user_id, rujukan_ktk_001())
    kasus.current_version.content = {**kasus.current_version.content, "legal_refs": [LAMA_KTK_001]}
    sesi.commit()
    approved_id = _setujui(sesi, kasus)
    baris_approved = _baris(sesi, approved_id)
    service.start_new_version(sesi, kasus.id, actor_id=user_id, is_admin=False)
    sesi.refresh(kasus)
    terbaca = service.get_case(sesi, kasus.id).legal_refs

    # Act
    rujukan = [item.model_dump(exclude={"display_text"}) for item in terbaca]
    service.update_case(sesi, kasus.id, _badan(*rujukan), actor_id=user_id, is_admin=False)
    sesi.refresh(kasus)

    # Assert
    assert [item.display_text for item in service.get_case(sesi, kasus.id).legal_refs] == [
        "Pasal 156 UU 13/2003"
    ]
    assert _baris(sesi, kasus.current_version_id) == [KTK_001]
    assert kasus.current_version.content["legal_refs"][0]["kind"] == "structured"
    approved = sesi.get(Case, kasus.id).latest_approved_version
    assert approved.id == approved_id
    assert approved.content["legal_refs"] == [LAMA_KTK_001]
    assert _baris(sesi, approved_id) == baris_approved
