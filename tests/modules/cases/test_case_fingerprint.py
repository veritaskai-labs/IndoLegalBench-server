"""Sidik jari isi kasus untuk kasus kembar saat impor. PBI-4 SCRUM-81 SCRUM-337.

Keputusan SA (D7, D8): judul dan pertanyaan yang sama setelah dinormalkan (huruf kecil,
spasi dirapikan) dianggap kasus yang sama. Fungsi murni, tanpa database.

Label: Positif = dianggap sama atau beda sesuai aturan, Edge = data batas.
"""

import pytest

from app.modules.cases.fingerprint import content_fingerprint

JUDUL = "PHK sepihak"
PERTANYAAN = "Apakah PHK tanpa surat sah?"


def test_sidik_jari_sha256_heksadesimal_64_karakter():
    """Positif: hasilnya muat di cases.content_fingerprint (varchar 64)."""
    # Act
    sidik = content_fingerprint(JUDUL, PERTANYAAN)

    # Assert
    assert sidik is not None
    assert len(sidik) == 64
    assert int(sidik, 16) >= 0


@pytest.mark.parametrize(
    ("judul", "pertanyaan"),
    [
        # Positif: huruf besar kecil tidak membedakan kasus.
        ("phk SEPIHAK", "APAKAH phk tanpa surat sah?"),
        # Positif: spasi di tepi, spasi ganda, tab, dan baris baru dirapikan (ekspor Sheets).
        ("  PHK   sepihak ", "Apakah PHK\ttanpa\n surat  sah?"),
    ],
)
def test_judul_dan_pertanyaan_yang_sama_setelah_dinormalkan_dianggap_kembar(judul, pertanyaan):
    """Positif: D7, kasus yang hanya beda huruf besar kecil atau spasi tetap kembar."""
    # Act / Assert
    assert content_fingerprint(judul, pertanyaan) == content_fingerprint(JUDUL, PERTANYAAN)


@pytest.mark.parametrize(
    ("judul", "pertanyaan"),
    [
        # Positif: pertanyaan berbeda berarti kasus berbeda walau judulnya sama.
        (JUDUL, "Apakah PHK lisan sah?"),
        # Positif: judul berbeda berarti kasus berbeda walau pertanyaannya sama.
        ("PHK massal", PERTANYAAN),
        # Edge: pemisah menjaga "ab" + "c" berbeda dari "a" + "bc".
        (JUDUL + " Apakah", "PHK tanpa surat sah?"),
    ],
)
def test_isi_yang_berbeda_tidak_dianggap_kembar(judul, pertanyaan):
    """Positif: hanya isi yang benar-benar sama yang kembar."""
    # Act / Assert
    assert content_fingerprint(judul, pertanyaan) != content_fingerprint(JUDUL, PERTANYAAN)


@pytest.mark.parametrize(("judul", "pertanyaan"), [(None, None), ("", "  "), ("\t", None)])
def test_judul_dan_pertanyaan_kosong_tidak_punya_sidik_jari(judul, pertanyaan):
    """Edge: draft impor boleh kosong (AC5); dua draft kosong tidak boleh saling melewati."""
    # Act / Assert
    assert content_fingerprint(judul, pertanyaan) is None


def test_satu_bagian_kosong_tetap_punya_sidik_jari():
    """Edge: kasus lama yang hanya punya pertanyaan tetap bisa dikenali kembarnya."""
    # Act
    sidik = content_fingerprint(None, PERTANYAAN)

    # Assert
    assert sidik is not None
    assert sidik == content_fingerprint("", PERTANYAAN)
