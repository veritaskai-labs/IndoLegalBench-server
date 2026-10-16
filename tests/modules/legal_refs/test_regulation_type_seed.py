"""Test pembaca seed jenis peraturan.

PBI-21, SCRUM-159 AC2. Dua kelompok: file seed asli di migrations/seeds
(menjaga datanya tetap utuh, 266 jenis dari JDIH BPK RI), dan file CSV
rusak buatan test (menjaga loader menolak data yang akan merusak tabel).
"""

import pytest

from app.modules.legal_refs import seed

HEADER = "urutan,code,label,singkatan,is_active"


def _tulis(tmp_path, *baris: str, header: str = HEADER, awal: str = ""):
    berkas = tmp_path / "seed.csv"
    berkas.write_text(awal + "\r\n".join([header, *baris]) + "\r\n", encoding="utf-8")
    return berkas


# --- file seed asli -----------------------------------------------------


@pytest.fixture(scope="module")
def jenis_asli():
    return seed.load_regulation_types(seed.DEFAULT_SEED_PATH)


def test_file_seed_asli_ada_di_migrations_seeds():
    # Act
    jalur = seed.DEFAULT_SEED_PATH

    # Assert
    assert jalur.name == "regulation_types_seed_2026-10-16.csv"
    assert jalur.parent.name == "seeds"
    assert jalur.exists()


def test_seed_asli_berisi_266_jenis_dengan_urutan_satu_sampai_266(jenis_asli):
    # Act
    urutan = [jenis.urutan for jenis in jenis_asli]

    # Assert
    assert urutan == list(range(1, 267))


def test_seed_asli_code_unik_dan_muat_di_kolom(jenis_asli):
    # Arrange
    kode = [jenis.code for jenis in jenis_asli]

    # Assert
    assert len(set(kode)) == 266
    assert max(len(isi) for isi in kode) == 113
    assert max(len(jenis.label) for jenis in jenis_asli) <= 255


def test_seed_asli_semua_jenis_aktif(jenis_asli):
    # Act
    status = [jenis.is_active for jenis in jenis_asli]

    # Assert
    assert all(aktif is True for aktif in status)


def test_seed_asli_singkatan_umum_terisi_dan_sisanya_kosong(jenis_asli):
    # Arrange
    per_code = {jenis.code: jenis for jenis in jenis_asli}

    # Assert
    assert per_code["uu"].label == "Undang-undang"
    assert per_code["uu"].singkatan == "UU"
    assert per_code["permen"].singkatan == "Permen"
    assert per_code["kepmen"].singkatan == "Kepmen"
    assert per_code["ketetapan_mpr"].singkatan is None
    assert sum(1 for jenis in jenis_asli if jenis.singkatan) == 14


def test_seed_asli_label_dengan_koma_tidak_terpotong(jenis_asli):
    # Arrange
    per_code = {jenis.code: jenis for jenis in jenis_asli}
    code = "peraturan_menteri_koordinator_bidang_politik_hukum_dan_keamanan"

    # Assert
    assert (
        per_code[code].label == "Peraturan Menteri Koordinator Bidang Politik, Hukum, dan Keamanan"
    )


# --- CSV rusak buatan test ----------------------------------------------


def test_baris_valid_dibaca_dengan_tipe_yang_benar(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, "1,uu,Undang-undang,UU,true", "2,ketetapan_mpr,Ketetapan MPR,,false")

    # Act
    hasil = seed.load_regulation_types(berkas)

    # Assert
    assert hasil == [
        seed.RegulationTypeSeed("uu", "Undang-undang", "UU", 1, True),
        seed.RegulationTypeSeed("ketetapan_mpr", "Ketetapan MPR", None, 2, False),
    ]


def test_bom_di_awal_file_tidak_merusak_nama_kolom(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, "1,uu,Undang-undang,UU,true", awal="﻿")

    # Act
    hasil = seed.load_regulation_types(berkas)

    # Assert
    assert [jenis.code for jenis in hasil] == ["uu"]


def test_spasi_di_sekitar_nilai_dibuang(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, " 1 , uu , Undang-undang , UU , TRUE ")

    # Act
    hasil = seed.load_regulation_types(berkas)

    # Assert
    assert hasil == [seed.RegulationTypeSeed("uu", "Undang-undang", "UU", 1, True)]


def test_header_yang_salah_ditolak_dengan_menyebut_kolom(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, "1,uu,Undang-undang,UU,true", header="urutan,code,label")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match="singkatan"):
        seed.load_regulation_types(berkas)


def test_file_tanpa_data_ditolak(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path)

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match="kosong"):
        seed.load_regulation_types(berkas)


def test_code_ganda_ditolak_dengan_menyebut_code_dan_baris(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, "1,uu,Undang-undang,UU,true", "2,uu,Lainnya,,true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 3.*uu"):
        seed.load_regulation_types(berkas)


def test_urutan_ganda_ditolak(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, "1,uu,Undang-undang,UU,true", "1,pp,Peraturan Pemerintah,PP,true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 3.*urutan"):
        seed.load_regulation_types(berkas)


@pytest.mark.parametrize("urutan", ["0", "-1", "satu", "1.5", ""])
def test_urutan_bukan_bilangan_bulat_positif_ditolak(tmp_path, urutan):
    # Arrange
    berkas = _tulis(tmp_path, f"{urutan},uu,Undang-undang,UU,true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*urutan"):
        seed.load_regulation_types(berkas)


@pytest.mark.parametrize("code", ["UU", "undang undang", "undang-undang", "", "uu;"])
def test_code_di_luar_huruf_kecil_angka_dan_garis_bawah_ditolak(tmp_path, code):
    # Arrange
    berkas = _tulis(tmp_path, f"1,{code},Undang-undang,UU,true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*code"):
        seed.load_regulation_types(berkas)


def test_code_150_karakter_masih_diterima_tapi_151_ditolak(tmp_path):
    # Arrange
    batas = "a" * 150
    terlalu_panjang = "a" * 151

    # Act
    diterima = seed.load_regulation_types(_tulis(tmp_path, f"1,{batas},Label,,true"))

    # Assert
    assert diterima[0].code == batas
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*code"):
        seed.load_regulation_types(_tulis(tmp_path, f"1,{terlalu_panjang},Label,,true"))


@pytest.mark.parametrize("label", ["", "   ", "x" * 256])
def test_label_kosong_atau_lebih_dari_255_karakter_ditolak(tmp_path, label):
    # Arrange
    berkas = _tulis(tmp_path, f"1,uu,{label},UU,true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*label"):
        seed.load_regulation_types(berkas)


def test_singkatan_lebih_dari_50_karakter_ditolak(tmp_path):
    # Arrange
    berkas = _tulis(tmp_path, f"1,uu,Undang-undang,{'S' * 51},true")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*singkatan"):
        seed.load_regulation_types(berkas)


@pytest.mark.parametrize("nilai", ["yes", "1", "", "benar"])
def test_is_active_selain_true_atau_false_ditolak(tmp_path, nilai):
    # Arrange
    berkas = _tulis(tmp_path, f"1,uu,Undang-undang,UU,{nilai}")

    # Act + Assert
    with pytest.raises(seed.SeedDataError, match=r"baris 2.*is_active"):
        seed.load_regulation_types(berkas)
