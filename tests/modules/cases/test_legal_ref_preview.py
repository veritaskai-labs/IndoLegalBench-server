"""Unit test service pratinjau rujukan hukum (PBI-21 AC6, AC8, AC10; SCRUM-162).

Murni, tanpa HTTP dan database: daftar jenis, kode tersimpan, dan tahun
berjalan diberikan langsung, jadi tidak perlu monkeypatch atau fixture DB.

Keputusan PO 18 Okt: satu hasil per rujukan, berisi teks rujukan (hanya kalau
rujukannya sah) atau masalah pertamanya (sama seperti saat simpan).

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Jenis "jenis_nonaktif", nomor "1", tahun 2020, pasal "IIII",
angka "1", dan teks bebas adalah input format sintetis.
"""

from typing import Any

from app.modules.cases.legal_ref_preview import preview_citations
from app.modules.cases.validation import (
    CONNECTOR_INVALID,
    FIELD_NOT_ALLOWED,
    FIELD_REQUIRED,
    REGULATION_TYPE_INACTIVE,
    REGULATION_TYPE_UNKNOWN,
    VALIDATION_ERROR,
    YEAR_IN_FUTURE,
)
from app.modules.legal_refs.service import TypeName
from tests.legal_refs import link_pasal, rujukan_ac10, rujukan_ktk_001

TAHUN_INI = 2026
AKTIF = {"uu": True, "jenis_nonaktif": False}
NAMA = {
    "uu": TypeName(label="Undang-Undang", singkatan="UU"),
    "jenis_nonaktif": TypeName(label="Jenis Nonaktif", singkatan=None),
}
TEKS_KTK_001 = "Pasal 156 UU 13/2003"
TEKS_AC10 = "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"


def _pratinjau(*rujukan: Any, tersimpan: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    hasil = preview_citations(
        list(rujukan), aktif=AKTIF, nama=NAMA, tersimpan=tersimpan, tahun_ini=TAHUN_INI
    )
    return [item.model_dump() for item in hasil]


def _teks(teks: str) -> dict[str, Any]:
    return {"display_text": teks, "error": None}


def _salah(code: str, field: str, message: str | None = None) -> dict[str, Any]:
    """Hasil untuk rujukan yang ditolak. Pesan hanya dicek kalau diberikan."""
    return {"display_text": None, "error": {"code": code, "field": field, "message": message}}


def _tanpa_pesan(hasil: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kosongkan pesan Pydantic (bahasa Inggris, bisa berubah antar versi) sebelum dibandingkan."""
    for item in hasil:
        if item["error"] is not None:
            item["error"]["message"] = None
    return hasil


def _nonaktif() -> dict[str, Any]:
    return {
        "kind": "structured",
        "links": [link_pasal("1", regulation_type="jenis_nonaktif", number="1", year=2020)],
    }


# --- positif -----------------------------------------------------------------


def test_ktk_001_ditulis_seperti_server():
    assert _pratinjau(rujukan_ktk_001()) == [_teks(TEKS_KTK_001)]


def test_contoh_ac10_dirangkai_dengan_jo():
    assert _pratinjau(rujukan_ac10()) == [_teks(TEKS_AC10)]


def test_teks_bebas_dikembalikan_apa_adanya_seperti_display_text_simpan():
    """Client yang menyembunyikan pratinjau Teks Bebas (keputusan PO no. 9)."""
    rujukan = {"kind": "free_text", "links": [], "free_text": "  Format teks bebas  "}

    assert _pratinjau(rujukan) == [_teks("Format teks bebas")]


def test_tahun_sama_dengan_tahun_berjalan_diterima():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["year"] = TAHUN_INI

    assert _pratinjau(rujukan) == [_teks(f"Pasal 156 UU 13/{TAHUN_INI}")]


def test_daftar_kosong_tanpa_hasil():
    """Minimal satu rujukan adalah aturan kasus (simpan dan zod), bukan aturan pratinjau."""
    assert _pratinjau() == []


# --- satu hasil per rujukan, urut sesuai kiriman -----------------------------


def test_rujukan_salah_tidak_menyembunyikan_rujukan_lain():
    rusak = rujukan_ktk_001()
    rusak["links"][0]["number"] = ""

    assert _pratinjau(rujukan_ktk_001(), rusak, rujukan_ac10()) == [
        _teks(TEKS_KTK_001),
        _salah(FIELD_REQUIRED, "legal_refs[1].links[0].number", "Nomor peraturan wajib diisi."),
        _teks(TEKS_AC10),
    ]


def test_hanya_masalah_pertama_per_rujukan():
    """Nomor kosong (schema) dan pasal kosong (aturan): seperti simpan, schema lebih dulu."""
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["number"] = ""
    rujukan["links"][0]["pasal"] = ""

    assert _pratinjau(rujukan) == [
        _salah(FIELD_REQUIRED, "legal_refs[0].links[0].number", "Nomor peraturan wajib diisi.")
    ]


# --- negatif: setiap kode error, teks null -----------------------------------


def test_pasal_kosong_untuk_bentuk_pasal():
    rujukan = rujukan_ktk_001()
    del rujukan["links"][0]["pasal"]

    assert _pratinjau(rujukan) == [
        _salah(
            FIELD_REQUIRED, "legal_refs[0].links[0].pasal", "Pasal wajib diisi untuk bentuk Pasal."
        )
    ]


def test_tahun_null_rujukan_lama():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["year"] = None

    assert _pratinjau(rujukan) == [
        _salah(FIELD_REQUIRED, "legal_refs[0].links[0].year", "Tahun wajib diisi.")
    ]


def test_format_pasal_salah_memakai_validation_error():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["pasal"] = "IIII"

    assert _tanpa_pesan(_pratinjau(rujukan)) == [
        _salah(VALIDATION_ERROR, "legal_refs[0].links[0].pasal")
    ]


def test_field_tidak_dipakai_bentuknya():
    rujukan = rujukan_ac10()
    rujukan["links"][0]["angka"] = "1"

    assert _pratinjau(rujukan) == [
        _salah(
            FIELD_NOT_ALLOWED,
            "legal_refs[0].links[0].angka",
            "Angka tidak boleh diisi untuk bentuk Pasal.",
        )
    ]


def test_peraturan_kedua_tanpa_penghubung():
    rujukan = rujukan_ac10()
    rujukan["links"][1]["connector"] = "none"

    assert _pratinjau(rujukan) == [
        _salah(
            CONNECTOR_INVALID,
            "legal_refs[0].links[1].connector",
            "Peraturan ke-2 wajib disambung dengan jo. atau jis.",
        )
    ]


def test_penghubung_jis_dan_campuran_diterima():
    """Keputusan PO no. 5: jo. dan jis. boleh dicampur dalam satu rangkaian."""
    rujukan = rujukan_ac10()
    rujukan["links"].append(link_pasal("1", number="1", year=2020, connector="jis"))

    assert _pratinjau(rujukan) == [_teks(f"{TEKS_AC10} jis. Pasal 1 UU 1/2020")]


def test_tahun_melewati_tahun_berjalan():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["year"] = TAHUN_INI + 1

    assert _pratinjau(rujukan) == [
        _salah(
            YEAR_IN_FUTURE, "legal_refs[0].links[0].year", f"Tahun tidak boleh melewati {TAHUN_INI}"
        )
    ]


def test_jenis_tidak_ada_di_daftar():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["regulation_type"] = "tidak_ada"

    assert _pratinjau(rujukan) == [
        _salah(
            REGULATION_TYPE_UNKNOWN,
            "legal_refs[0].links[0].regulation_type",
            "Jenis peraturan tidak ada di daftar",
        )
    ]


def test_jenis_nonaktif_ditolak_untuk_rujukan_baru():
    assert _pratinjau(_nonaktif()) == [
        _salah(
            REGULATION_TYPE_INACTIVE,
            "legal_refs[0].links[0].regulation_type",
            "Jenis peraturan ini sudah tidak aktif. Pilih jenis lain.",
        )
    ]


def test_jenis_nonaktif_diterima_kalau_sudah_tersimpan_di_versi_itu():
    """Aturan versi yang sama (keputusan PO 17 Okt), sama seperti update_case."""
    assert _pratinjau(_nonaktif(), tersimpan=frozenset({"jenis_nonaktif"})) == [
        _teks("Pasal 1 Jenis Nonaktif Nomor 1 Tahun 2020")
    ]


# --- corner: isi rujukan bukan objek -----------------------------------------


def test_rujukan_null():
    assert _pratinjau(rujukan_ktk_001(), None) == [
        _teks(TEKS_KTK_001),
        _salah(FIELD_REQUIRED, "legal_refs[1]", "Rujukan ke-2 wajib diisi."),
    ]


def test_rujukan_bukan_objek():
    assert _tanpa_pesan(_pratinjau("x")) == [_salah(VALIDATION_ERROR, "legal_refs[0]")]


def test_kind_tidak_dikenal():
    assert _tanpa_pesan(_pratinjau({"kind": "lain", "links": []})) == [
        _salah(VALIDATION_ERROR, "legal_refs[0].kind")
    ]
