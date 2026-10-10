"""Unit test pembuat file unduhan audit log (PBI-18 AC6).

Fungsi murni: daftar AuditLogRead masuk, bytes CSV atau PDF keluar.
Isi wajib per ticket: waktu (WIB), pelaku, aksi, objek, dan nilai
sebelum/sesudah.
"""

import csv
import io
import re
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.audit.export import (
    CsvExporter,
    ExportFormat,
    PdfExporter,
    export_filename,
    exporter_for,
)
from app.modules.audit.models import AuditEntityType
from app.modules.audit.schemas import AuditLogRead

DIUNDUH = datetime(2026, 10, 8, 3, 15, tzinfo=UTC)
OBJEK = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def _catatan(**kolom) -> AuditLogRead:
    nilai = {
        "id": 1,
        "occurred_at": datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        "actor_user_id": uuid.UUID("00000000-0000-0000-0000-0000000000b1"),
        "actor_name": "Rina Sari",
        "actor_role": "author",
        "action": "case.updated",
        "entity_type": AuditEntityType.CASE,
        "entity_id": OBJEK,
        "case_id": OBJEK,
        "before": {"title": "Lama"},
        "after": {"title": "Baru"},
        "reason": None,
        "request_id": None,
    }
    nilai.update(kolom)
    return AuditLogRead(**nilai)


def _baca_csv(isi: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(isi.decode("utf-8-sig"))))


# --- CSV ----------------------------------------------------------------------


def test_csv_berisi_kolom_wajib_ticket():
    # Act
    header, baris = _baca_csv(CsvExporter().render([_catatan()], generated_at=DIUNDUH))

    # Assert
    assert header == [
        "Waktu (WIB)",
        "Pelaku",
        "Peran",
        "Aksi",
        "Jenis objek",
        "ID objek",
        "Sebelum",
        "Sesudah",
        "Alasan",
    ]
    assert baris == [
        "2026-10-08 09:00:00 WIB",
        "Rina Sari",
        "author",
        "case.updated",
        "case",
        str(OBJEK),
        '{"title": "Lama"}',
        '{"title": "Baru"}',
        "",
    ]


def test_csv_catatan_pembuatan_tanpa_nilai_sebelum():
    # Act
    _, baris = _baca_csv(
        CsvExporter().render([_catatan(action="case.created", before=None)], generated_at=DIUNDUH)
    )

    # Assert
    assert (baris[6], baris[7]) == ("", '{"title": "Baru"}')


def test_csv_diawali_bom_supaya_excel_membaca_utf8():
    # Act
    isi = CsvExporter().render([], generated_at=DIUNDUH)

    # Assert
    assert isi.startswith(b"\xef\xbb\xbf")
    assert len(_baca_csv(isi)) == 1


def test_csv_pelaku_sistem_ditulis_sistem():
    # Act
    _, baris = _baca_csv(
        CsvExporter().render(
            [_catatan(actor_user_id=None, actor_name=None, actor_role=None)], generated_at=DIUNDUH
        )
    )

    # Assert
    assert (baris[1], baris[2]) == ("Sistem", "")


def test_csv_koma_kutip_dan_baris_baru_tetap_utuh():
    # Arrange
    alasan = 'Reviewer A cuti, diganti "C"\nsesuai rapat'

    # Act
    _, baris = _baca_csv(CsvExporter().render([_catatan(reason=alasan)], generated_at=DIUNDUH))

    # Assert
    assert baris[8] == alasan


@pytest.mark.parametrize("awal", ["=", "+", "-", "@"])
def test_csv_dilindungi_dari_formula_injection(awal):
    """Sel yang diawali karakter formula tidak dieksekusi spreadsheet penerima."""
    # Act
    _, baris = _baca_csv(
        CsvExporter().render([_catatan(reason=f"{awal}HYPERLINK(1)")], generated_at=DIUNDUH)
    )

    # Assert
    assert baris[8] == f"'{awal}HYPERLINK(1)"


def test_csv_nilai_json_non_ascii_tidak_di_escape():
    # Act
    _, baris = _baca_csv(
        CsvExporter().render([_catatan(after={"judul": "Pasal 1 — ayat 2"})], generated_at=DIUNDUH)
    )

    # Assert
    assert baris[7] == '{"judul": "Pasal 1 — ayat 2"}'


# --- PDF ----------------------------------------------------------------------


def test_pdf_valid_dan_memuat_isi_catatan():
    # Act
    isi = PdfExporter(compress=False).render([_catatan()], generated_at=DIUNDUH)

    # Assert
    assert isi.startswith(b"%PDF")
    assert b"Jejak Audit IndoLegalBench" in isi
    assert b"Rina Sari" in isi
    # Sel waktu dibungkus jadi dua baris di kolom sempit, jadi cukup nilainya.
    assert b"2026-10-08 09:00:00" in isi
    assert b"Diunduh 2026-10-08 10:15 WIB" in isi


def test_pdf_tanpa_catatan_tetap_jadi_file():
    # Act
    isi = PdfExporter(compress=False).render([], generated_at=DIUNDUH)

    # Assert
    assert b"Tidak ada catatan" in isi


def test_pdf_banyak_catatan_berlanjut_ke_halaman_berikut():
    # Arrange
    banyak = [_catatan(id=nomor) for nomor in range(1, 80)]

    # Act
    isi = PdfExporter(compress=False).render(banyak, generated_at=DIUNDUH)

    # Assert
    assert len(re.findall(rb"/Type /Page\b", isi)) > 1


def test_pdf_teks_mirip_markup_tidak_merusak_file():
    """Paragraph reportlab membaca tag XML. Isi catatan harus di-escape."""
    # Act
    isi = PdfExporter(compress=False).render(
        [_catatan(reason="<b>tebal</b> & <unknown>")], generated_at=DIUNDUH
    )

    # Assert
    assert isi.startswith(b"%PDF")


def test_pdf_default_terkompresi_lebih_kecil():
    # Arrange
    banyak = [_catatan(id=nomor) for nomor in range(1, 30)]

    # Act
    kecil = PdfExporter().render(banyak, generated_at=DIUNDUH)
    besar = PdfExporter(compress=False).render(banyak, generated_at=DIUNDUH)

    # Assert
    assert len(kecil) < len(besar)


# --- Pemilihan format ---------------------------------------------------------


@pytest.mark.parametrize(
    ("format_", "media_type"),
    [(ExportFormat.CSV, "text/csv; charset=utf-8"), (ExportFormat.PDF, "application/pdf")],
)
def test_pilih_pembuat_file_per_format(format_, media_type):
    # Act + Assert
    assert exporter_for(format_).media_type == media_type


@pytest.mark.parametrize(
    ("format_", "nama"),
    [("csv", "audit-log-20261008-1015.csv"), ("pdf", "audit-log-20261008-1015.pdf")],
)
def test_nama_file_memakai_waktu_wib(format_, nama):
    # Act + Assert
    assert export_filename(ExportFormat(format_), DIUNDUH) == nama
