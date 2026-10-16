"""Test tabel regulation_types, legal_refs, dan legal_ref_links.

PBI-21, SCRUM-159. Bentuk tabel mengikuti tiket: model datar dengan
satu nomor peraturan per link. Aturan yang dijaga database diuji di sini.
Aturan isian per bentuk rujukan (pasal wajib untuk bentuk pasal, dan
seterusnya) dijaga validasi di SCRUM-160, bukan di sini.

SQLite tidak menegakkan foreign key, jadi FK diperiksa lewat metadata.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.legal_refs import models
from app.shared.database import Base

VERSI_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
VERSI_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _simpan(db_session, baris):
    db_session.add(baris)
    db_session.commit()
    return baris


def _jenis(db_session, *, code: str = "uu", urutan: int = 3, **kolom):
    return _simpan(
        db_session,
        models.RegulationType(code=code, label=f"Label {code}", urutan=urutan, **kolom),
    )


def _rujukan(db_session, *, order: int = 1, version=VERSI_A, **kolom):
    kolom.setdefault("form", models.LegalRefForm.PASAL)
    return _simpan(db_session, models.LegalRef(case_version_id=version, order=order, **kolom))


def _link(db_session, rujukan_id, *, position: int = 1, **kolom):
    kolom.setdefault("connector", models.LegalRefConnector.NONE)
    kolom.setdefault("regulation_type", "uu")
    kolom.setdefault("number", "13")
    kolom.setdefault("year", 2003)
    return _simpan(
        db_session,
        models.LegalRefLink(legal_ref_id=rujukan_id, position=position, **kolom),
    )


@pytest.mark.parametrize("tabel", ["regulation_types", "legal_refs", "legal_ref_links"])
def test_tabel_terdaftar_di_metadata(tabel):
    assert tabel in Base.metadata.tables


def test_nilai_enum_form_dan_connector_mengikuti_tiket():
    assert [bentuk.value for bentuk in models.LegalRefForm] == [
        "name_only",
        "pasal",
        "pasal_angka",
        "free_text",
    ]
    assert [penghubung.value for penghubung in models.LegalRefConnector] == [
        "none",
        "jo",
        "jis",
    ]


# --- regulation_types ---------------------------------------------------


def test_jenis_peraturan_baru_aktif_dan_singkatan_boleh_kosong(db_session):
    # Act
    baris = _jenis(db_session, code="ketetapan_mpr", urutan=2)

    # Assert
    assert baris.is_active is True
    assert baris.singkatan is None


def test_code_dan_label_cukup_panjang_untuk_data_seed():
    # Arrange
    kolom = models.RegulationType.__table__.c

    # Assert: code terpanjang di seed 113 karakter, label 113 karakter
    assert kolom.code.type.length >= 150
    assert kolom.label.type.length >= 255


def test_code_adalah_kunci_utama_jadi_tidak_boleh_ganda(db_session):
    # Arrange
    _jenis(db_session, code="uu", urutan=3)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _jenis(db_session, code="uu", urutan=4)


def test_urutan_tidak_boleh_ganda(db_session):
    # Arrange
    _jenis(db_session, code="uu", urutan=3)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _jenis(db_session, code="pp", urutan=3)


def test_jenis_peraturan_bisa_dinonaktifkan_tanpa_menghapus_baris(db_session):
    # Arrange
    baris = _jenis(db_session, code="uu", urutan=3)

    # Act
    baris.is_active = False
    db_session.commit()

    # Assert
    assert db_session.get(models.RegulationType, "uu").is_active is False


# --- legal_refs ---------------------------------------------------------


def test_rujukan_menyimpan_bentuk_dan_urutan_pada_versi_kasus(db_session):
    # Act
    baris = _rujukan(db_session, order=1, form=models.LegalRefForm.PASAL_ANGKA)

    # Assert
    assert baris.id is not None
    assert baris.case_version_id == VERSI_A
    assert baris.order == 1
    assert baris.form == models.LegalRefForm.PASAL_ANGKA
    assert baris.free_text is None


def test_urutan_rujukan_tidak_boleh_ganda_dalam_satu_versi(db_session):
    # Arrange
    _rujukan(db_session, order=1)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _rujukan(db_session, order=1)


def test_urutan_rujukan_yang_sama_boleh_dipakai_versi_lain(db_session):
    # Arrange
    _rujukan(db_session, order=1, version=VERSI_A)

    # Act
    baris = _rujukan(db_session, order=1, version=VERSI_B)

    # Assert
    assert baris.case_version_id == VERSI_B


@pytest.mark.parametrize("urutan", [0, -1])
def test_urutan_rujukan_dimulai_dari_satu(db_session, urutan):
    with pytest.raises(IntegrityError):
        _rujukan(db_session, order=urutan)


def test_teks_bebas_disimpan_apa_adanya(db_session):
    # Arrange
    isi = "  Putusan MA No. 12 K/Pdt/2020, hlm. 5  "

    # Act
    baris = _rujukan(db_session, form=models.LegalRefForm.FREE_TEXT, free_text=isi)

    # Assert
    assert baris.free_text == isi


@pytest.mark.parametrize("isi", [None, "", "   "])
def test_bentuk_teks_bebas_wajib_berisi_teks(db_session, isi):
    with pytest.raises(IntegrityError):
        _rujukan(db_session, form=models.LegalRefForm.FREE_TEXT, free_text=isi)


@pytest.mark.parametrize("bentuk", ["NAME_ONLY", "PASAL", "PASAL_ANGKA"])
def test_bentuk_terstruktur_tidak_boleh_membawa_teks_bebas(db_session, bentuk):
    with pytest.raises(IntegrityError):
        _rujukan(db_session, form=models.LegalRefForm[bentuk], free_text="sisa teks")


# --- legal_ref_links ----------------------------------------------------


def test_link_menyimpan_seluruh_bagian_peraturan(db_session):
    # Arrange
    _jenis(db_session, code="uu", urutan=3)
    rujukan = _rujukan(db_session, form=models.LegalRefForm.PASAL_ANGKA)

    # Act
    link = _link(
        db_session,
        rujukan.id,
        about="Ketenagakerjaan",
        pasal="156",
        angka="5",
        ayat="2",
        huruf="a",
    )

    # Assert
    assert (link.regulation_type, link.number, link.year) == ("uu", "13", 2003)
    assert (link.about, link.pasal, link.angka) == ("Ketenagakerjaan", "156", "5")
    assert (link.ayat, link.huruf) == ("2", "a")


def test_bagian_opsional_link_kosong_secara_default(db_session):
    # Arrange
    rujukan = _rujukan(db_session, form=models.LegalRefForm.NAME_ONLY)

    # Act
    link = _link(db_session, rujukan.id)

    # Assert
    assert link.about is None
    assert link.pasal is None
    assert link.angka is None
    assert link.ayat is None
    assert link.huruf is None


def test_regulation_type_link_merujuk_ke_code_jenis_peraturan():
    # Arrange
    kolom = models.LegalRefLink.__table__.c.regulation_type

    # Act
    target = {fk.target_fullname for fk in kolom.foreign_keys}

    # Assert
    assert target == {"regulation_types.code"}
    assert kolom.nullable is False


def test_link_pertama_wajib_tanpa_penghubung(db_session):
    # Arrange
    rujukan = _rujukan(db_session)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, position=1, connector=models.LegalRefConnector.JO)


def test_link_berikutnya_wajib_memakai_jo_atau_jis(db_session):
    # Arrange
    rujukan = _rujukan(db_session)
    _link(db_session, rujukan.id, position=1)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, position=2, connector=models.LegalRefConnector.NONE)


@pytest.mark.parametrize("nama_penghubung", ["JO", "JIS"])
def test_rujukan_bisa_merangkai_dua_peraturan(db_session, nama_penghubung):
    # Arrange
    penghubung = models.LegalRefConnector[nama_penghubung]
    rujukan = _rujukan(db_session, form=models.LegalRefForm.PASAL_ANGKA)
    _link(db_session, rujukan.id, position=1, pasal="156")

    # Act
    kedua = _link(
        db_session,
        rujukan.id,
        position=2,
        connector=penghubung,
        number="6",
        year=2023,
        pasal="II",
        angka="5",
    )

    # Assert
    assert kedua.connector == penghubung
    assert [link.position for link in rujukan.links] == [1, 2]


def test_posisi_link_tidak_boleh_ganda_dalam_satu_rujukan(db_session):
    # Arrange
    rujukan = _rujukan(db_session)
    _link(db_session, rujukan.id, position=1)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, position=1)


def test_posisi_link_dimulai_dari_satu(db_session):
    rujukan = _rujukan(db_session)

    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, position=0)


@pytest.mark.parametrize("tahun", [0, -2003, 10000])
def test_tahun_di_luar_rentang_ditolak(db_session, tahun):
    rujukan = _rujukan(db_session)

    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, year=tahun)


@pytest.mark.parametrize("kolom", ["regulation_type", "number"])
def test_jenis_dan_nomor_link_wajib_ada(db_session, kolom):
    # Arrange
    rujukan = _rujukan(db_session)

    # Act + Assert
    with pytest.raises(IntegrityError):
        _link(db_session, rujukan.id, **{kolom: None})


def test_tahun_boleh_kosong_agar_rujukan_lama_tanpa_tahun_tetap_terstruktur(db_session):
    # Arrange: tahun opsional di Sprint 1, jadi data lama bisa tidak punya tahun
    rujukan = _rujukan(db_session)

    # Act
    link = _link(db_session, rujukan.id, year=None)
    db_session.refresh(link)

    # Assert
    assert link.year is None
    assert models.LegalRefLink.__table__.c.year.nullable is True


def test_link_dibaca_berurutan_menurut_posisi(db_session):
    # Arrange
    rujukan = _rujukan(db_session)
    _link(db_session, rujukan.id, position=2, connector=models.LegalRefConnector.JO)
    _link(db_session, rujukan.id, position=1)
    db_session.refresh(rujukan)

    # Act
    posisi = [link.position for link in rujukan.links]

    # Assert
    assert posisi == [1, 2]


def test_menghapus_rujukan_ikut_menghapus_link_miliknya(db_session):
    # Arrange
    rujukan = _rujukan(db_session)
    _link(db_session, rujukan.id, position=1)

    # Act
    db_session.delete(rujukan)
    db_session.commit()

    # Assert
    assert db_session.query(models.LegalRefLink).count() == 0
