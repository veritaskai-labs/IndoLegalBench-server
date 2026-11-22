"""Pemeriksaan satu baris impor CSV.

Tingkat baris (gagal = baris itu saja): legal_refs, must_contain, must_not_contain harus list JSON
yang terparse, dan panjang teks tidak melebihi batas. Semua field boleh kosong karena hasilnya
draft; kelengkapan diperiksa saat diajukan review. Rujukan hukum boleh campuran terstruktur dan
teks bebas; teks biasa disimpan sebagai rujukan free_text.
"""

import json

import pytest

from app.modules.cases.csv_import import (
    TEMPLATE_COLUMNS,
    ImportedCase,
    RawRow,
    RowFailure,
    parse_row,
)
from app.modules.cases.fingerprint import content_fingerprint
from app.modules.cases.models import SplitTag
from tests.legal_refs import rujukan_pasal

JENIS = {"uu": True, "pp": True, "kepres_lama": False}
TAHUN_INI = 2026


def _mentah(row_no: int = 2, **isi: str) -> RawRow:
    return RawRow(row_no=row_no, cells=tuple(isi.get(kolom, "") for kolom in TEMPLATE_COLUMNS))


def _baca(**isi: str):
    return parse_row(_mentah(**isi), regulation_types=JENIS, current_year=TAHUN_INI)


def _kasus(**isi: str) -> ImportedCase:
    hasil = _baca(**isi)
    assert isinstance(hasil, ImportedCase), hasil
    return hasil


def _gagal(**isi: str) -> str:
    hasil = _baca(**isi)
    assert isinstance(hasil, RowFailure), hasil
    assert hasil.row_no == 2
    return hasil.reason


def test_baris_lengkap_menjadi_isi_kasus():
    """Semua kolom terisi, teks dirapikan, list JSON terparse."""
    kasus = _kasus(
        title="  PHK sepihak ",
        question="Apakah PHK tanpa surat sah?",
        category="ketenagakerjaan",
        split_tag="dev",
        legal_refs=json.dumps([rujukan_pasal("151")]),
        must_contain='["surat", " pesangon "]',
        must_not_contain='["sah"]',
        expected_conclusion=" tidak sah ",
    )

    assert (kasus.row_no, kasus.title, kasus.question, kasus.category) == (
        2,
        "PHK sepihak",
        "Apakah PHK tanpa surat sah?",
        "ketenagakerjaan",
    )
    assert kasus.split_tag is SplitTag.DEV
    assert kasus.legal_refs[0]["kind"] == "structured"
    assert kasus.legal_refs[0]["links"][0]["pasal"] == "151"
    assert (kasus.must_contain, kasus.must_not_contain, kasus.expected_conclusion) == (
        ["surat", "pesangon"],
        ["sah"],
        "tidak sah",
    )


def test_semua_field_boleh_kosong_kecuali_satu():
    """Kasus lama punya field lebih sedikit; draft dilengkapi nanti."""
    kasus = _kasus(title="Hanya judul")

    assert (kasus.question, kasus.category, kasus.split_tag) == ("", None, None)
    assert (kasus.legal_refs, kasus.must_contain, kasus.must_not_contain) == ([], [], [])
    assert kasus.expected_conclusion is None


def test_tag_kosong_dibiarkan_kosong_untuk_dipilih_setelah_impor():
    """Tag ditentukan setelah impor."""
    assert _kasus(title="A", split_tag="  ").split_tag is None


@pytest.mark.parametrize(("isi", "tag"), [("test", SplitTag.TEST), (" DEV ", SplitTag.DEV)])
def test_tag_dibaca_tanpa_peduli_huruf_besar(isi, tag):
    """Google Sheets sering mengubah huruf pertama jadi besar."""
    assert _kasus(title="A", split_tag=isi).split_tag is tag


def test_rujukan_campuran_terstruktur_dan_teks_bebas():
    """Teks biasa disimpan sebagai rujukan free_text, urutan dijaga."""
    kasus = _kasus(
        title="A",
        legal_refs=json.dumps(["Pasal 1 KUHPerdata", rujukan_pasal("151")]),
    )

    assert kasus.legal_refs[0] == {
        "kind": "free_text",
        "free_text": "Pasal 1 KUHPerdata",
        "links": [],
    }
    assert kasus.legal_refs[1]["kind"] == "structured"


def test_objek_free_text_tanpa_links_diterima():
    """Penulis CSV tidak perlu menulis links kosong untuk rujukan teks bebas."""
    kasus = _kasus(title="A", legal_refs='[{"kind": "free_text", "free_text": "UU Cipta Kerja"}]')

    assert kasus.legal_refs == [{"kind": "free_text", "free_text": "UU Cipta Kerja", "links": []}]


def test_kutip_ganda_di_dalam_list_json_dari_google_sheets():
    """Kutip ganda di dalam teks list JSON di-escape."""
    kasus = _kasus(title="A", must_contain='["frasa \\"kunci\\""]')

    assert kasus.must_contain == ['frasa "kunci"']


def test_isi_list_yang_kosong_dibuang():
    """Sel list yang tersisa string kosong dari Sheets tidak menjadi frasa kosong."""
    assert _kasus(title="A", must_contain='["surat", "  ", ""]').must_contain == ["surat"]


def test_sidik_jari_dari_judul_dan_pertanyaan():
    """Kembar dikenali dari judul dan pertanyaan yang dinormalkan."""
    kasus = _kasus(title="PHK  Sepihak", question="Sah?")

    assert kasus.fingerprint == content_fingerprint("phk sepihak", "sah?")


def test_batas_panjang_tepat_diterima():
    """Tepat di batas CaseWrite (300, 20000, 120) masih diterima."""
    kasus = _kasus(title="j" * 300, question="p" * 20000, category="k" * 120)

    assert (len(kasus.title), len(kasus.question), len(kasus.category)) == (300, 20000, 120)


def test_payload_untuk_kelengkapan_mengikuti_bentuk_casewrite():
    """Kelengkapan dihitung dengan aturan yang sama dengan kasus manual."""
    kasus = _kasus(title="A", question="Q", split_tag="test", must_contain='["x"]')

    assert kasus.as_payload("IMPOR-1") == {
        "case_code": "IMPOR-1",
        "identity": {"title": "A", "question": "Q", "category": None},
        "split_tag": "test",
        "legal_refs": [],
        "answer_criteria": {
            "must_contain": ["x"],
            "must_not_contain": [],
            "expected_conclusion": None,
        },
    }


def test_jumlah_kolom_salah_gagal():
    """Baris dengan kolom kurang tidak bisa dipetakan ke template."""
    hasil = parse_row(
        RawRow(row_no=7, cells=("A", "B")), regulation_types=JENIS, current_year=TAHUN_INI
    )

    assert hasil == RowFailure(row_no=7, reason="Jumlah kolom 2, seharusnya 8 sesuai template.")


@pytest.mark.parametrize(
    ("kolom", "panjang", "batas"),
    [("title", 301, 300), ("question", 20001, 20000), ("category", 121, 120)],
)
def test_teks_melebihi_batas_gagal(kolom, panjang, batas):
    """Panjang teks tidak boleh melebihi batas skema kasus."""
    alasan = _gagal(**{kolom: "x" * panjang})

    assert alasan == f"{kolom}: maksimal {batas} karakter (sekarang {panjang})."


def test_tag_tidak_dikenal_gagal():
    """Tag hanya dev atau test."""
    assert _gagal(title="A", split_tag="validasi") == "split_tag: isi dev, test, atau kosongkan."


@pytest.mark.parametrize("kolom", ["legal_refs", "must_contain", "must_not_contain"])
def test_list_json_rusak_gagal(kolom):
    """Kolom list harus list JSON yang terparse."""
    alasan = _gagal(title="A", **{kolom: "[surat"})

    assert alasan.startswith(f"{kolom}: bukan list JSON yang valid.")


@pytest.mark.parametrize("kolom", ["legal_refs", "must_contain", "must_not_contain"])
def test_json_bukan_list_gagal(kolom):
    """Objek atau teks JSON tunggal bukan list."""
    assert _gagal(title="A", **{kolom: '"surat"'}).startswith(f"{kolom}: harus list JSON")


def test_isi_kriteria_bukan_teks_gagal():
    """Frasa kriteria jawaban harus teks."""
    assert _gagal(title="A", must_contain='["ok", 3]') == "must_contain[1]: harus teks."


def test_rujukan_bukan_teks_atau_objek_gagal():
    """Rujukan hanya teks bebas atau objek terstruktur."""
    assert _gagal(title="A", legal_refs="[42]") == "legal_refs[0]: harus teks atau objek rujukan."


def test_rujukan_teks_kosong_gagal():
    """Rujukan teks bebas yang kosong tidak berarti apa-apa."""
    assert _gagal(title="A", legal_refs='["  "]').startswith("legal_refs[0].free_text")


def test_rujukan_terstruktur_tanpa_field_wajib_gagal():
    """Aturan simpan rujukan tetap berlaku untuk rujukan terstruktur hasil impor."""
    rujukan = rujukan_pasal("151")
    del rujukan["links"][0]["number"]

    assert "legal_refs[0].links[0].number" in _gagal(title="A", legal_refs=json.dumps([rujukan]))


def test_rujukan_terstruktur_tahun_masa_depan_gagal():
    """Aturan tahun rujukan (tidak melewati tahun berjalan WIB)."""
    rujukan = rujukan_pasal("151", year=2099)

    alasan = _gagal(title="A", legal_refs=json.dumps([rujukan]))

    assert alasan == "legal_refs[0].links[0].year: Tahun tidak boleh melewati 2026"


def test_jenis_peraturan_tidak_dikenal_gagal():
    """Jenis harus ada di daftar."""
    rujukan = rujukan_pasal("151", regulation_type="perda_mars")

    alasan = _gagal(title="A", legal_refs=json.dumps([rujukan]))

    assert alasan == "legal_refs[0].links[0].regulation_type: Jenis peraturan tidak ada di daftar"


def test_jenis_peraturan_nonaktif_gagal():
    """Jenis nonaktif hanya boleh pada rujukan yang sudah tersimpan; impor selalu baru."""
    rujukan = rujukan_pasal("151", regulation_type="kepres_lama")

    assert "sudah tidak aktif" in _gagal(title="A", legal_refs=json.dumps([rujukan]))


def test_rujukan_teks_bebas_terlalu_panjang_gagal():
    """Rujukan free_text maksimal 2000 karakter (skema rujukan)."""
    assert _gagal(title="A", legal_refs=json.dumps(["x" * 2001])).startswith(
        "legal_refs[0].free_text"
    )


def test_hanya_masalah_pertama_yang_dilaporkan():
    """Satu alasan per baris, urut kolom template, supaya ringkasan tetap terbaca."""
    assert _gagal(title="x" * 301, split_tag="salah").startswith("title:")
