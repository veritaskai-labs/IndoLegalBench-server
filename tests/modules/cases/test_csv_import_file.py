"""Template dan pemeriksaan tingkat berkas impor CSV. PBI-4 SCRUM-81 SCRUM-338, AC4-AC6, diagram D7.

AC6: template CSV bisa diunduh. D7 tingkat berkas: ekstensi .csv, UTF-8 (dengan atau tanpa BOM),
header sama dengan template, maksimal 500 baris. Gagal di tingkat berkas = seluruh berkas ditolak,
tidak ada yang disimpan. Baris yang seluruhnya kosong dilewati tanpa dicatat.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas.
"""

import csv
import io

import pytest

from app.modules.cases.csv_import import (
    IMPORT_FILE_EMPTY,
    IMPORT_FILE_NOT_CSV,
    IMPORT_FILE_NOT_UTF8,
    IMPORT_FILE_TOO_LARGE,
    IMPORT_FILE_UNREADABLE,
    IMPORT_HEADER_MISMATCH,
    IMPORT_TOO_MANY_ROWS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    TEMPLATE_COLUMNS,
    ImportFileError,
    read_file,
    template_csv,
)

KOLOM = list(TEMPLATE_COLUMNS)


def _berkas(*baris: list[str], header: list[str] | None = None, bom: bool = False) -> bytes:
    keluaran = io.StringIO()
    penulis = csv.writer(keluaran, lineterminator="\r\n")
    penulis.writerow(KOLOM if header is None else header)
    for isi in baris:
        penulis.writerow(isi)
    data = keluaran.getvalue().encode("utf-8")
    return b"\xef\xbb\xbf" + data if bom else data


def _baris(**isi: str) -> list[str]:
    return [isi.get(kolom, "") for kolom in KOLOM]


def _tolak(nama: str, data: bytes) -> ImportFileError:
    with pytest.raises(ImportFileError) as galat:
        read_file(nama, data)
    return galat.value


# Template (AC6)


def test_template_berisi_delapan_kolom_skema_kasus_tanpa_case_code():
    """Positif: D7, kolom template mengikuti CaseWrite; case_code dibuat sistem."""
    assert KOLOM == [
        "title",
        "question",
        "category",
        "split_tag",
        "legal_refs",
        "must_contain",
        "must_not_contain",
        "expected_conclusion",
    ]


def test_template_utf8_dengan_bom_dan_hanya_header():
    """Positif: template UTF-8 (BOM supaya Excel tidak salah membaca), pemisah koma, satu baris
    header saja, jadi mengunggah template kosong tidak membuat kasus."""
    isi = template_csv()

    assert isi.startswith(b"\xef\xbb\xbf")
    teks = isi.decode("utf-8-sig")
    assert list(csv.reader(io.StringIO(teks))) == [KOLOM]


def test_template_yang_diunggah_kembali_ditolak_karena_tidak_ada_baris():
    """Edge: template apa adanya lolos header, tetapi tidak ada baris kasus."""
    galat = _tolak("template.csv", template_csv())

    assert galat.code == IMPORT_FILE_EMPTY


# Baris yang dibaca


def test_baris_dibaca_dengan_nomor_baris_spreadsheet():
    """Positif: nomor baris sama dengan spreadsheet (header = baris 1), supaya alasan gagal
    mudah dicari di Google Sheets."""
    hasil = read_file("kasus.csv", _berkas(_baris(title="A"), _baris(title="B")))

    assert [(baris.row_no, baris.cells[0]) for baris in hasil] == [(2, "A"), (3, "B")]


def test_bom_google_sheets_atau_excel_diterima():
    """Positif: D7, UTF-8 dengan atau tanpa BOM."""
    hasil = read_file("kasus.csv", _berkas(_baris(title="Judul"), bom=True))

    assert hasil[0].cells[0] == "Judul"


def test_header_boleh_berspasi_dan_huruf_besar():
    """Edge: Google Sheets kadang menyisakan spasi; nama kolom dibandingkan setelah dirapikan."""
    header = [f" {kolom.upper()} " for kolom in KOLOM]

    hasil = read_file("kasus.csv", _berkas(_baris(title="A"), header=header))

    assert len(hasil) == 1


def test_isi_bertanda_kutip_dan_baris_baru_tetap_satu_baris():
    """Edge: D7, list JSON dengan kutip ganda ter-escape dan teks multi-baris dari Sheets."""
    hasil = read_file(
        "kasus.csv",
        _berkas(
            _baris(title="A", question="baris 1\nbaris 2", must_contain='["\\"kutip\\""]'),
            _baris(title="B"),
        ),
    )

    assert [baris.row_no for baris in hasil] == [2, 3]
    assert hasil[0].cells[1] == "baris 1\nbaris 2"
    assert hasil[0].cells[5] == '["\\"kutip\\""]'


def test_baris_kosong_dilewati_tanpa_dicatat_tetapi_nomor_baris_tetap():
    """Edge: D7, baris yang seluruhnya kosong (termasuk hanya spasi) dilewati."""
    hasil = read_file(
        "kasus.csv", _berkas(_baris(title="A"), _baris(), _baris(title="  "), _baris(title="B"))
    )

    assert [baris.row_no for baris in hasil] == [2, 5]


def test_jumlah_kolom_yang_salah_tetap_dibaca_untuk_dilaporkan_per_baris():
    """Edge: kolom kurang atau lebih adalah masalah baris, bukan seluruh berkas."""
    hasil = read_file("kasus.csv", _berkas(["hanya", "dua"]))

    assert hasil[0].cells == ("hanya", "dua")


def test_tepat_500_baris_diterima():
    """Edge: batas AC4, sekitar 500 kasus per berkas."""
    hasil = read_file("kasus.csv", _berkas(*[_baris(title=f"K{i}") for i in range(MAX_ROWS)]))

    assert len(hasil) == MAX_ROWS == 500


def test_baris_kosong_tidak_dihitung_dalam_batas_500():
    """Edge: baris kosong dilewati, jadi tidak memakan jatah 500."""
    baris = [_baris(title=f"K{i}") for i in range(MAX_ROWS)] + [_baris(), _baris()]

    assert len(read_file("kasus.csv", _berkas(*baris))) == MAX_ROWS


# Berkas ditolak utuh (D7)


@pytest.mark.parametrize("nama", ["kasus.xlsx", "kasus.txt", "kasus", "kasus.csv.exe"])
def test_bukan_csv_ditolak(nama):
    """Negatif: D7, ekstensi harus .csv."""
    galat = _tolak(nama, _berkas(_baris(title="A")))

    assert galat.code == IMPORT_FILE_NOT_CSV
    assert ".csv" in galat.message


def test_ekstensi_csv_huruf_besar_diterima():
    """Edge: KASUS.CSV dari Windows tetap CSV."""
    assert len(read_file("KASUS.CSV", _berkas(_baris(title="A")))) == 1


def test_bukan_utf8_ditolak():
    """Negatif: D7, berkas Latin-1 atau Windows-1252 ditolak dengan petunjuk ekspor UTF-8."""
    data = _berkas(_baris(title="Pasal")).replace(b"Pasal", "Pasal é".encode("latin-1"))

    galat = _tolak("kasus.csv", data)

    assert galat.code == IMPORT_FILE_NOT_UTF8
    assert "UTF-8" in galat.message


def test_header_berbeda_ditolak_dan_menyebut_header_yang_diharapkan():
    """Negatif: D7, header harus sama dengan template; pemisah titik koma juga jatuh di sini."""
    galat = _tolak("kasus.csv", _berkas(_baris(title="A"), header=KOLOM[:-1]))

    assert galat.code == IMPORT_HEADER_MISMATCH
    assert galat.details == {"expected": KOLOM}
    assert "koma" in galat.message


def test_pemisah_titik_koma_ditolak_sebagai_header_berbeda():
    """Negatif: ekspor Excel berlokal Indonesia memakai titik koma."""
    data = (";".join(KOLOM) + "\r\nA;B;;;;;;\r\n").encode()

    assert _tolak("kasus.csv", data).code == IMPORT_HEADER_MISMATCH


def test_berkas_kosong_ditolak():
    """Negatif: berkas tanpa header sama sekali."""
    assert _tolak("kasus.csv", b"").code == IMPORT_HEADER_MISMATCH


def test_header_tanpa_baris_kasus_ditolak():
    """Negatif: tidak ada yang bisa diimpor, jadi batch dan suite tidak dibuat."""
    galat = _tolak("kasus.csv", _berkas(_baris(), _baris()))

    assert galat.code == IMPORT_FILE_EMPTY


def test_lebih_dari_500_baris_ditolak():
    """Negatif: AC4, D7, maksimal 500 baris per berkas."""
    galat = _tolak("kasus.csv", _berkas(*[_baris(title=f"K{i}") for i in range(MAX_ROWS + 1)]))

    assert galat.code == IMPORT_TOO_MANY_ROWS
    assert "500" in galat.message


def test_berkas_terlalu_besar_ditolak_sebelum_dibaca():
    """Negatif: batas byte menjaga server dari berkas raksasa."""
    galat = _tolak("kasus.csv", b"a" * (MAX_FILE_BYTES + 1))

    assert galat.code == IMPORT_FILE_TOO_LARGE


def test_karakter_nul_ditolak():
    """Negatif: PostgreSQL tidak bisa menyimpan NUL di teks; berkas ditolak, bukan error 500."""
    galat = _tolak("kasus.csv", _berkas(_baris(title="A\x00B")))

    assert galat.code == IMPORT_FILE_UNREADABLE


def test_tanda_kutip_rusak_ditolak():
    """Negatif: teks setelah kutip penutup berarti CSV rusak; berkas ditolak utuh."""
    data = (",".join(KOLOM) + '\r\n"ab"c,,,,,,,\r\n').encode()

    galat = _tolak("kasus.csv", data)

    assert galat.code == IMPORT_FILE_UNREADABLE
