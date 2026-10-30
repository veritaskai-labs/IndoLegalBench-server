"""Penyimpanan ganda rujukan hukum (PBI-21, SCRUM-160).

Keputusan PO 17 Okt: setiap simpan menulis content["legal_refs"] dan tabel
legal_refs/legal_ref_links SCRUM-159 dalam satu transaksi lewat satu fungsi
tulis; tabel hanya ditulis ulang untuk versi yang masih terbuka; versi baru
dari versi approved ikut mendapat barisnya. Bacaan tetap dari content.

Contoh hukum hanya KTK-001 dan contoh AC10 (belum dikonfirmasi tim legal).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.cases import repository
from app.modules.cases.models import Case
from app.modules.legal_refs.models import LegalRef
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.modules.cases.test_cases import _badan, _buat, _setujui, _suite

TEKS_BEBAS = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}


def _baris(db_session, version_id: uuid.UUID) -> list[tuple]:
    """Isi tabel untuk satu versi, urut order lalu position, tanpa id."""
    db_session.expire_all()
    rujukan = db_session.scalars(
        select(LegalRef).where(LegalRef.case_version_id == version_id).order_by(LegalRef.order)
    ).all()
    return [
        (
            item.order,
            item.kind.value,
            item.free_text,
            [
                (
                    link.position,
                    link.connector.value,
                    link.form.value,
                    link.regulation_type,
                    link.number,
                    link.year,
                    link.pasal,
                    link.angka,
                    link.ayat,
                    link.huruf,
                    link.about,
                )
                for link in item.links
            ],
        )
        for item in rujukan
    ]


BARIS_KTK_001 = (
    1,
    "structured",
    None,
    [(1, "none", "pasal", "uu", "13", 2003, "156", None, None, None, None)],
)
LINK_AC10 = [
    (1, "none", "pasal", "uu", "13", 2003, "156", None, None, None, None),
    (2, "jo", "pasal_angka", "uu", "6", 2023, "II", "5", None, None, None),
]


def _versi(db_session, case_id: str):
    db_session.expire_all()
    return db_session.get(Case, uuid.UUID(case_id)).current_version


def test_kasus_baru_menulis_tabel_sesuai_urutan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    daftar = [rujukan_ktk_001(), rujukan_ac10(), TEKS_BEBAS]

    # Act
    case_id = _buat(client, _suite(client), legal_refs=daftar).json()["id"]

    # Assert
    assert _baris(db_session, _versi(db_session, case_id).id) == [
        BARIS_KTK_001,
        (2, "structured", None, LINK_AC10),
        (3, "free_text", "Format teks bebas", []),
    ]


def test_mengubah_kasus_mengganti_baris_bukan_menambah(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ac10(), TEKS_BEBAS]).json()["id"]

    # Act
    respons = client.put(f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001()]))

    # Assert
    assert respons.status_code == 200, respons.text
    assert _baris(db_session, _versi(db_session, case_id).id) == [BARIS_KTK_001]


def test_field_opsional_link_ikut_tersimpan(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    rujukan = rujukan_ktk_001()
    rujukan["links"][0].update(about="Format tentang", ayat="1", huruf="a")

    # Act
    case_id = _buat(client, _suite(client), legal_refs=[rujukan]).json()["id"]

    # Assert
    link = _baris(db_session, _versi(db_session, case_id).id)[0][3][0]
    assert link[-3:] == ("1", "a", "Format tentang")


def test_simpan_yang_gagal_tidak_mengubah_tabel(as_role, db_session):
    """Satu transaksi: kode kasus bentrok (409) membatalkan content dan baris tabel."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    _buat(client, suite_id, case_code="PHK-002")
    case_id = _buat(client, suite_id, legal_refs=[rujukan_ac10()]).json()["id"]

    # Act
    respons = client.put(
        f"/cases/{case_id}", json=_badan(case_code="PHK-002", legal_refs=[rujukan_ktk_001()])
    )

    # Assert
    assert respons.status_code == 409
    assert _baris(db_session, _versi(db_session, case_id).id) == [
        (1, "structured", None, LINK_AC10)
    ]


def test_versi_baru_dari_approved_mendapat_baris_sendiri(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ac10()]).json()["id"]
    approved_id = _versi(db_session, case_id).id
    _setujui(db_session, case_id)

    # Act
    respons = client.post(f"/cases/{case_id}/versions")
    draf_id = _versi(db_session, case_id).id

    # Assert
    assert respons.status_code == 201, respons.text
    assert draf_id != approved_id
    assert _baris(db_session, draf_id) == [(1, "structured", None, LINK_AC10)]
    assert _baris(db_session, approved_id) == [(1, "structured", None, LINK_AC10)]


def test_mengubah_draf_baru_tidak_menyentuh_baris_versi_approved(as_role, db_session):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client), legal_refs=[rujukan_ac10()]).json()["id"]
    approved_id = _versi(db_session, case_id).id
    _setujui(db_session, case_id)
    client.post(f"/cases/{case_id}/versions")

    # Act
    client.put(f"/cases/{case_id}", json=_badan(legal_refs=[rujukan_ktk_001()]))

    # Assert
    assert _baris(db_session, approved_id) == [(1, "structured", None, LINK_AC10)]
    assert _baris(db_session, _versi(db_session, case_id).id) == [BARIS_KTK_001]


def test_versi_baru_dari_isi_sprint_1_ditulis_dalam_bentuk_baru(as_role, db_session):
    """Versi approved lama yang content-nya bentuk Sprint 1 (teks jenis "UU")."""
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    versi = _versi(db_session, case_id)
    lama = {"regulation_type": "UU", "regulation_number": "13", "year": 2003, "pasal": "156"}
    versi.content = {**versi.content, "legal_refs": [lama]}
    db_session.commit()
    _setujui(db_session, case_id)

    # Act
    client.post(f"/cases/{case_id}/versions")

    # Assert
    assert _baris(db_session, _versi(db_session, case_id).id) == [BARIS_KTK_001]


# --- error database lain tidak disamarkan jadi "kode kasus sudah dipakai" -------
# Satu commit sekarang juga menulis baris legal_refs. Hanya uq_cases_case_code
# (PostgreSQL) atau cases.case_code (SQLite) yang berarti kode bentrok.

PELANGGARAN_LAIN = (
    'new row for relation "legal_refs" violates check constraint '
    '"ck_legal_refs_free_text_only_for_free_text_kind"'
)


def _gagal_dengan(pesan: str):
    def gagal(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception(pesan))

    return gagal


def test_pelanggaran_constraint_lain_saat_membuat_tidak_dilaporkan_sebagai_kode_bentrok(
    as_role, monkeypatch
):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    monkeypatch.setattr(repository, "create", _gagal_dengan(PELANGGARAN_LAIN))

    # Act + Assert
    with pytest.raises(IntegrityError, match="ck_legal_refs_free_text_only_for_free_text_kind"):
        _buat(client, suite_id)


def test_pelanggaran_constraint_lain_saat_mengubah_tidak_dilaporkan_sebagai_kode_bentrok(
    as_role, monkeypatch
):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    monkeypatch.setattr(repository, "save", _gagal_dengan(PELANGGARAN_LAIN))

    # Act + Assert
    with pytest.raises(IntegrityError, match="ck_legal_refs"):
        client.put(f"/cases/{case_id}", json=_badan())


def test_bentrok_kode_versi_sqlite_tetap_case_code_taken(as_role, monkeypatch):
    # Arrange
    client = as_role(Role.CONTRIBUTOR)
    suite_id = _suite(client)
    monkeypatch.setattr(
        repository, "create", _gagal_dengan("UNIQUE constraint failed: cases.case_code")
    )

    # Act
    respons = _buat(client, suite_id)

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "CASE_CODE_TAKEN"
