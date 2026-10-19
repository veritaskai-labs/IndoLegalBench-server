"""Kode kasus untuk modul audit (F-06, TC-31, PBI-18 AC3).

Audit tidak boleh membaca repository cases, jadi cases.service
menyediakan dua fungsi: kode kasus per id untuk kolom Objek, dan id
kasus dari sebagian kode untuk saringan "Kode kasus".
"""

import uuid

from app.modules.cases import service
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _suite


def _dua_kasus(as_role) -> dict[str, uuid.UUID]:
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    return {
        kode: uuid.UUID(_buat(client, suite_id, case_code=kode).json()["id"])
        for kode in ("UAT-PHK-001", "UAT-PHK-002")
    }


def test_kode_kasus_per_id(as_role, db_session):
    ids = _dua_kasus(as_role)

    hasil = service.case_codes(db_session, set(ids.values()))

    assert hasil == {case_id: kode for kode, case_id in ids.items()}


def test_id_tak_dikenal_tidak_ikut(as_role, db_session):
    ids = _dua_kasus(as_role)

    hasil = service.case_codes(db_session, {ids["UAT-PHK-001"], uuid.uuid4()})

    assert hasil == {ids["UAT-PHK-001"]: "UAT-PHK-001"}


def test_tanpa_id_tanpa_query(db_session):
    assert service.case_codes(db_session, set()) == {}


def test_cari_sebagian_kode_tanpa_peduli_kapital(as_role, db_session):
    ids = _dua_kasus(as_role)

    assert service.find_case_ids(db_session, "phk-001") == [ids["UAT-PHK-001"]]
    assert sorted(service.find_case_ids(db_session, "uat-phk")) == sorted(ids.values())


def test_kode_tak_dikenal_daftar_kosong(as_role, db_session):
    _dua_kasus(as_role)

    assert service.find_case_ids(db_session, "TIDAK-ADA") == []


def test_wildcard_dicari_apa_adanya(as_role, db_session):
    _dua_kasus(as_role)

    assert service.find_case_ids(db_session, "%") == []
    assert service.find_case_ids(db_session, "UAT_PHK") == []
