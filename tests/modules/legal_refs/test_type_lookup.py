"""Test pencari code jenis peraturan.

PBI-21, SCRUM-159 AC9. Rujukan Sprint 1 menyimpan jenis peraturan sebagai
teks bebas (mis. "UU"). Tabel baru merujuk code, jadi teks itu dicocokkan
ke code, lalu singkatan, lalu label, tanpa membedakan huruf besar atau kecil.
"""

import pytest

from app.modules.legal_refs import seed, type_lookup


def _jenis(code, label, singkatan=None, urutan=1, is_active=True):
    return seed.RegulationTypeSeed(code, label, singkatan, urutan, is_active)


@pytest.fixture
def indeks():
    return type_lookup.RegulationTypeIndex(
        [
            _jenis("uu", "Undang-undang", "UU", 1),
            _jenis("peraturan_pemerintah", "Peraturan Pemerintah", "PP", 2),
            _jenis("ketetapan_mpr", "Ketetapan MPR", None, 3),
            _jenis("lama", "Peraturan Lama", None, 4, is_active=False),
        ]
    )


@pytest.mark.parametrize(
    ("teks", "diharapkan"),
    [
        ("uu", "uu"),
        ("peraturan_pemerintah", "peraturan_pemerintah"),
        ("UU", "uu"),
        ("pp", "peraturan_pemerintah"),
        ("Undang-undang", "uu"),
        ("UNDANG-UNDANG", "uu"),
        ("Peraturan Pemerintah", "peraturan_pemerintah"),
        ("Ketetapan MPR", "ketetapan_mpr"),
    ],
)
def test_teks_dicocokkan_ke_code_singkatan_atau_label(indeks, teks, diharapkan):
    # Act
    hasil = indeks.resolve(teks)

    # Assert
    assert hasil == diharapkan


def test_spasi_di_pinggir_dan_spasi_ganda_diabaikan(indeks):
    # Arrange
    teks = "  Peraturan   Pemerintah \t"

    # Act
    hasil = indeks.resolve(teks)

    # Assert
    assert hasil == "peraturan_pemerintah"


@pytest.mark.parametrize("teks", ["", "   ", "Peraturan Tidak Ada", "UU 13"])
def test_teks_yang_tidak_dikenal_menghasilkan_none(indeks, teks):
    # Act
    hasil = indeks.resolve(teks)

    # Assert
    assert hasil is None


def test_jenis_nonaktif_tetap_bisa_dicocokkan(indeks):
    # Act
    hasil = indeks.resolve("Peraturan Lama")

    # Assert
    assert hasil == "lama"


def test_code_menang_atas_singkatan_dan_label_yang_sama():
    # Arrange: "perda" adalah code satu jenis dan label jenis lain
    indeks = type_lookup.RegulationTypeIndex(
        [
            _jenis("perda", "Peraturan Daerah", "Perda", 1),
            _jenis("lainnya", "Perda", None, 2),
        ]
    )

    # Act
    hasil = indeks.resolve("Perda")

    # Assert
    assert hasil == "perda"


def test_singkatan_menang_atas_label_yang_sama():
    # Arrange: "pp" adalah singkatan satu jenis dan label jenis lain
    indeks = type_lookup.RegulationTypeIndex(
        [
            _jenis("peraturan_pemerintah", "Peraturan Pemerintah", "PP", 1),
            _jenis("lainnya", "PP", None, 2),
        ]
    )

    # Act
    hasil = indeks.resolve("pp")

    # Assert
    assert hasil == "peraturan_pemerintah"


# --- seed asli ----------------------------------------------------------


@pytest.fixture(scope="module")
def indeks_asli():
    return type_lookup.RegulationTypeIndex(seed.load_regulation_types())


def test_seed_asli_setiap_code_dan_label_kembali_ke_jenisnya_sendiri(indeks_asli):
    # Arrange
    jenis = seed.load_regulation_types()

    # Act
    salah = [
        (item.code, kolom)
        for item in jenis
        for kolom, teks in (("code", item.code), ("label", item.label))
        if indeks_asli.resolve(teks) != item.code
    ]

    # Assert
    assert salah == []


@pytest.mark.parametrize(
    ("teks", "diharapkan"),
    [
        ("UU", "uu"),
        ("Undang-Undang", "uu"),
        ("PP", "pp"),
        ("Perpres", "perpres"),
        ("Permen", "permen"),
        ("Kepmen", "kepmen"),
        ("Perda", "perda"),
    ],
)
def test_seed_asli_menyelesaikan_penulisan_yang_lazim(indeks_asli, teks, diharapkan):
    # Act
    hasil = indeks_asli.resolve(teks)

    # Assert
    assert hasil == diharapkan
