"""Approved question text per split tag across suites. SCRUM-76 SCRUM-244.

The measurement summary prices the question on the latest approved version.
content["question"] is that text. A later draft does not replace it, a
draft-only case is left out, and the lists stay ordered by case_code.
"""

import uuid

from app.modules.cases import service as cases_service
from app.modules.cases.models import Case, SplitTag
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite


def _identitas(pertanyaan: str) -> dict:
    return {
        "title": "PHK sepihak",
        "question": pertanyaan,
        "category": "ketenagakerjaan",
    }


def _kasus_approved(
    client, db_session, suite_id: str, case_code: str, split_tag: str, pertanyaan: str
) -> str:
    case_id = _buat(
        client,
        suite_id,
        case_code=case_code,
        split_tag=split_tag,
        identity=_identitas(pertanyaan),
    ).json()["id"]
    _setujui(db_session, case_id)
    return case_id


def test_hanya_pertanyaan_approved_per_split_diurutkan_kode_kasus(as_role, db_session):
    # Arrange: dua dev dan satu test yang approved, satu dev yang masih draf.
    # PHK-002 dibuat lebih dulu supaya urutan kode, bukan urutan simpan, yang diuji.
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    _kasus_approved(client, db_session, suite_id, "PHK-002", "dev", "Pertanyaan kedua")
    _kasus_approved(client, db_session, suite_id, "PHK-001", "dev", "Pertanyaan pertama")
    _kasus_approved(client, db_session, suite_id, "PHK-003", "test", "Pertanyaan uji")
    assert (
        _buat(
            client,
            suite_id,
            case_code="PHK-004",
            split_tag="dev",
            identity=_identitas("Pertanyaan draf"),
        ).status_code
        == 201
    )

    # Act
    teks = cases_service.approved_questions_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert
    assert teks == {
        SplitTag.DEV: ["Pertanyaan pertama", "Pertanyaan kedua"],
        SplitTag.TEST: ["Pertanyaan uji"],
    }


def test_draf_baru_setelah_persetujuan_tidak_mengganti_pertanyaan_approved(as_role, db_session):
    # Arrange: kasus approved, lalu penulis membuka draf dan mengganti pertanyaannya
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _kasus_approved(
        client, db_session, suite_id, "PHK-001", "dev", "Apakah PHK tanpa surat sah?"
    )
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    draf = _badan(case_code="PHK-001", split_tag="dev")
    draf["identity"] = {**draf["identity"], "question": "Pertanyaan draf yang belum disetujui?"}
    assert client.put(f"/cases/{case_id}", json=draf).status_code == 200

    # Act
    teks = cases_service.approved_questions_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert
    assert teks == {
        SplitTag.DEV: ["Apakah PHK tanpa surat sah?"],
        SplitTag.TEST: [],
    }


def test_split_dan_pertanyaan_dibaca_dari_versi_approved_bukan_dari_draf(as_role, db_session):
    # Arrange: lewat API kedua tag selalu berubah bersama (PBI-9 AC3), jadi
    # perbedaannya disiapkan langsung di database untuk membuktikan query-nya
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    case_id = _kasus_approved(
        client, db_session, suite_id, "PHK-001", "dev", "Pertanyaan yang disetujui"
    )
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    kasus = db_session.get(Case, uuid.UUID(case_id))
    isi = dict(kasus.current_version.content)
    isi["question"] = "Pertanyaan draf yang belum disetujui?"
    kasus.current_version.content = isi
    kasus.current_version.split_tag = SplitTag.TEST
    db_session.commit()
    assert kasus.latest_approved_version.split_tag == SplitTag.DEV

    # Act
    teks = cases_service.approved_questions_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert: tetap dev, dan teksnya teks yang disetujui
    assert teks == {SplitTag.DEV: ["Pertanyaan yang disetujui"], SplitTag.TEST: []}


def test_menjumlahkan_beberapa_suite_dan_mengabaikan_suite_lain(as_role, db_session):
    # Arrange: tiga suite, hanya dua yang diminta
    client = as_role(Role.CONTRIBUTOR)
    suite_a = _suite(client, "Suite A")
    suite_b = _suite(client, "Suite B")
    suite_lain = _suite(client, "Suite lain")
    _kasus_approved(client, db_session, suite_a, "PHK-002", "dev", "Pertanyaan A")
    _kasus_approved(client, db_session, suite_b, "PHK-001", "test", "Pertanyaan B")
    _kasus_approved(client, db_session, suite_lain, "PHK-003", "dev", "Pertanyaan lain")

    # Act
    teks = cases_service.approved_questions_by_split(
        db_session, {uuid.UUID(suite_a), uuid.UUID(suite_b)}
    )

    # Assert: PHK-001 lebih dulu dari PHK-002, suite lain tidak ikut
    assert teks == {SplitTag.DEV: ["Pertanyaan A"], SplitTag.TEST: ["Pertanyaan B"]}


def test_suite_hanya_berisi_draf_menghasilkan_daftar_kosong(as_role, db_session):
    # Arrange: suite berisi satu draf saja
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    assert _buat(client, suite_id, case_code="PHK-001").status_code == 201

    # Act
    teks = cases_service.approved_questions_by_split(db_session, {uuid.UUID(suite_id)})

    # Assert: kedua kunci selalu ada
    assert teks == {SplitTag.DEV: [], SplitTag.TEST: []}


def test_daftar_suite_kosong_menghasilkan_daftar_kosong_tanpa_query_error(db_session):
    # Arrange: tidak ada data

    # Act
    teks = cases_service.approved_questions_by_split(db_session, set())

    # Assert
    assert teks == {SplitTag.DEV: [], SplitTag.TEST: []}


def test_suite_yang_tidak_dikenal_menghasilkan_daftar_kosong_bukan_error(db_session):
    # Arrange: id acak yang tidak ada di database

    # Act
    teks = cases_service.approved_questions_by_split(db_session, {uuid.uuid4()})

    # Assert: validasi keberadaan suite bukan tugas modul cases
    assert teks == {SplitTag.DEV: [], SplitTag.TEST: []}
