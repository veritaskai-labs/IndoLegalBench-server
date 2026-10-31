"""Count of approved cases per split tag across suites. SCRUM-206.

The measurement summary only counts a case when it has an approved
version. The split tag that decides dev or test is the one on that
approved version, so a draft that is being edited never moves a case.
"""

import uuid

from app.modules.cases import service as cases_service
from app.modules.cases.models import Case, SplitTag
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite


def _kasus_approved(client, db_session, suite_id: str, case_code: str, split_tag: str) -> str:
    case_id = _buat(client, suite_id, case_code=case_code, split_tag=split_tag).json()["id"]
    _setujui(db_session, case_id)
    return case_id


def test_hanya_kasus_approved_dihitung_per_split_tag(as_role, db_session):
    # Arrange: dua dev dan satu test yang approved, satu dev yang masih draf
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    _kasus_approved(client, db_session, suite_id, "PHK-001", "dev")
    _kasus_approved(client, db_session, suite_id, "PHK-002", "dev")
    _kasus_approved(client, db_session, suite_id, "PHK-003", "test")
    assert _buat(client, suite_id, case_code="PHK-004", split_tag="dev").status_code == 201

    # Act
    counts = cases_service.count_approved_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert
    assert counts == {SplitTag.DEV: 2, SplitTag.TEST: 1}


def test_kasus_dengan_draf_baru_tetap_dihitung_satu_kali(as_role, db_session):
    # Arrange: kasus dev approved, lalu penulis membuka dan mengedit draf baru
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _kasus_approved(client, db_session, suite_id, "PHK-001", "dev")
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    draf = _badan(case_code="PHK-001", split_tag="dev")
    draf["identity"] = {**draf["identity"], "title": "Judul draf yang belum disetujui"}
    assert client.put(f"/cases/{case_id}", json=draf).status_code == 200

    # Act
    counts = cases_service.count_approved_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert: satu kasus, bukan dua baris untuk dua versi
    assert counts == {SplitTag.DEV: 1, SplitTag.TEST: 0}


def test_split_tag_dibaca_dari_versi_approved_bukan_dari_draf(as_role, db_session):
    # Arrange: lewat API kedua tag selalu berubah bersama (PBI-9 AC3), jadi
    # perbedaannya disiapkan langsung di database untuk membuktikan query-nya
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _kasus_approved(client, db_session, suite_id, "PHK-001", "dev")
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    kasus = db_session.get(Case, uuid.UUID(case_id))
    kasus.current_version.split_tag = SplitTag.TEST
    db_session.commit()
    assert kasus.latest_approved_version.split_tag == SplitTag.DEV

    # Act
    counts = cases_service.count_approved_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert: tetap dev, karena versi approved terbaru masih dev
    assert counts == {SplitTag.DEV: 1, SplitTag.TEST: 0}


def test_menjumlahkan_beberapa_suite_dan_mengabaikan_suite_lain(as_role, db_session):
    # Arrange: tiga suite, hanya dua yang diminta
    client = as_role(Role.CONTRIBUTOR)
    suite_a = _suite(client, "Suite A")
    suite_b = _suite(client, "Suite B")
    suite_lain = _suite(client, "Suite lain")
    _kasus_approved(client, db_session, suite_a, "PHK-001", "dev")
    _kasus_approved(client, db_session, suite_b, "PHK-002", "test")
    _kasus_approved(client, db_session, suite_lain, "PHK-003", "dev")

    # Act
    counts = cases_service.count_approved_by_split(
        db_session, {uuid.UUID(suite_a), uuid.UUID(suite_b)}
    )

    # Assert
    assert counts == {SplitTag.DEV: 1, SplitTag.TEST: 1}


def test_suite_tanpa_kasus_approved_menghasilkan_nol_untuk_kedua_split(as_role, db_session):
    # Arrange: suite berisi satu draf saja
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    assert _buat(client, suite_id, case_code="PHK-001").status_code == 201

    # Act
    counts = cases_service.count_approved_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert: kedua kunci selalu ada supaya pemanggil tidak perlu get(..., 0)
    assert counts == {SplitTag.DEV: 0, SplitTag.TEST: 0}


def test_daftar_suite_kosong_menghasilkan_nol_tanpa_query_error(db_session):
    # Arrange: tidak ada data

    # Act
    counts = cases_service.count_approved_by_split(db_session, set())

    # Assert
    assert counts == {SplitTag.DEV: 0, SplitTag.TEST: 0}


def test_suite_yang_tidak_dikenal_dihitung_nol_bukan_error(db_session):
    # Arrange: id acak yang tidak ada di database

    # Act
    counts = cases_service.count_approved_by_split(db_session, {uuid.uuid4()})

    # Assert: validasi keberadaan suite bukan tugas modul cases
    assert counts == {SplitTag.DEV: 0, SplitTag.TEST: 0}
