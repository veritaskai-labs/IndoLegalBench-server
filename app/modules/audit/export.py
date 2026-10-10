"""Pembuat file unduhan audit log, CSV dan PDF (PBI-18 AC6).

Satu kelas per format (pola Strategy). Router memilih lewat
exporter_for(), dan kedua format memakai kolom yang sama dari
_kolom(), jadi isi CSV dan PDF tidak pernah berbeda.

Isi wajib per ticket: waktu (WIB), pelaku, aksi, objek, dan nilai
sebelum/sesudah. Modul ini murni: tidak ada database dan HTTP.
"""

import csv
import io
import json
from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.audit.schemas import WIB, AuditLogRead

HEADER = [
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
_JUDUL = "Jejak Audit IndoLegalBench"
# Karakter yang membuat spreadsheet membaca sel sebagai formula (CSV injection).
_AWAL_FORMULA = ("=", "+", "-", "@", "\t", "\r")


class ExportFormat(StrEnum):
    CSV = "csv"
    PDF = "pdf"


class Exporter(ABC):
    media_type: str
    format: ExportFormat

    @abstractmethod
    def render(self, rows: list[AuditLogRead], *, generated_at: datetime) -> bytes:
        """Bytes file siap unduh."""


class CsvExporter(Exporter):
    media_type = "text/csv; charset=utf-8"
    format = ExportFormat.CSV

    def render(self, rows: list[AuditLogRead], *, generated_at: datetime) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow([_aman_untuk_spreadsheet(nilai) for nilai in _kolom(row)])
        # BOM supaya Excel membaca UTF-8, bukan ANSI.
        return buffer.getvalue().encode("utf-8-sig")


class PdfExporter(Exporter):
    media_type = "application/pdf"
    format = ExportFormat.PDF
    # Lebar kolom dalam mm, total 277 mm = lebar A4 lanskap dikurangi margin.
    _LEBAR = (30, 28, 18, 34, 20, 30, 45, 45, 27)

    def __init__(self, *, compress: bool = True) -> None:
        self.compress = compress

    def render(self, rows: list[AuditLogRead], *, generated_at: datetime) -> bytes:
        styles = getSampleStyleSheet()
        sel = styles["BodyText"].clone("sel", fontSize=7, leading=8.5)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
            title=_JUDUL,
            pageCompression=1 if self.compress else 0,
        )
        isi = [
            Paragraph(_JUDUL, styles["Title"]),
            Paragraph(
                f"Diunduh {generated_at.astimezone(WIB):%Y-%m-%d %H:%M} WIB · {len(rows)} catatan",
                styles["Normal"],
            ),
            Spacer(1, 4 * mm),
        ]
        if not rows:
            isi.append(Paragraph("Tidak ada catatan untuk saringan ini.", styles["Normal"]))
        else:
            data = [[Paragraph(f"<b>{judul}</b>", sel) for judul in HEADER]]
            data += [[Paragraph(escape(nilai), sel) for nilai in _kolom(row)] for row in rows]
            tabel = Table(data, colWidths=[lebar * mm for lebar in self._LEBAR], repeatRows=1)
            tabel.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EAED")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            isi.append(tabel)
        doc.build(isi)
        return buffer.getvalue()


_EXPORTERS: dict[ExportFormat, Exporter] = {
    ExportFormat.CSV: CsvExporter(),
    ExportFormat.PDF: PdfExporter(),
}


def exporter_for(format_: ExportFormat) -> Exporter:
    return _EXPORTERS[format_]


def export_filename(format_: ExportFormat, generated_at: datetime) -> str:
    return f"audit-log-{generated_at.astimezone(WIB):%Y%m%d-%H%M}.{format_.value}"


def _kolom(row: AuditLogRead) -> list[str]:
    """Satu baris file, urutannya sama dengan HEADER."""
    pelaku = "Sistem" if row.actor_user_id is None else row.actor_name or str(row.actor_user_id)
    return [
        f"{row.occurred_at.astimezone(WIB):%Y-%m-%d %H:%M:%S} WIB",
        pelaku,
        row.actor_role or "",
        row.action,
        row.entity_type.value,
        str(row.entity_id),
        _json(row.before),
        _json(row.after),
        row.reason or "",
    ]


def _json(nilai: dict[str, Any] | None) -> str:
    if nilai is None:
        return ""
    return json.dumps(nilai, ensure_ascii=False, sort_keys=True)


def _aman_untuk_spreadsheet(nilai: str) -> str:
    if nilai.startswith(_AWAL_FORMULA):
        return "'" + nilai
    return nilai
