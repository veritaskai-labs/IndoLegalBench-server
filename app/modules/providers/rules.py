"""Isian wajib dan terlarang per jenis pengukuran produk AI (PBI-23 AC1-AC3, SCRUM-201).

Aturan murni tanpa database. Service memanggilnya sebelum menyimpan, sehingga
isian yang tidak sesuai jenis menjadi 422 dengan field yang jelas. Constraint C1
di database (D1) tetap ada sebagai pengaman terakhir.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.modules.providers.models import MeasurementType

FIELD_REQUIRED = "FIELD_REQUIRED"
FIELD_NOT_ALLOWED = "FIELD_NOT_ALLOWED"

# Isian khusus tiap jenis (D1 C1). Wajib dulu, opsional sesudahnya.
_WAJIB = {
    MeasurementType.AUTOMATIC: (
        "provider_type",
        "base_url",
        "model_name",
        "credential",
        "rate_limit_per_minute",
        "monthly_budget_idr",
    ),
    MeasurementType.MANUAL: ("vendor_name", "site_url"),
}
_OPSIONAL = {
    MeasurementType.AUTOMATIC: (
        "model_params",
        "price_input_usd_per_mtok",
        "price_output_usd_per_mtok",
    ),
    MeasurementType.MANUAL: ("access_note", "manual_params"),
}
TYPE_FIELDS = {jenis: _WAJIB[jenis] + _OPSIONAL[jenis] for jenis in MeasurementType}

# Nama isian seperti di form produk, supaya pesan bisa ditampilkan apa adanya.
_LABEL = {
    "provider_type": "Jenis penyedia",
    "base_url": "Base URL",
    "model_name": "Nama model",
    "credential": "Kredensial",
    "rate_limit_per_minute": "Batas panggilan per menit",
    "monthly_budget_idr": "Batas biaya bulanan",
    "model_params": "Parameter model",
    "price_input_usd_per_mtok": "Harga input",
    "price_output_usd_per_mtok": "Harga output",
    "vendor_name": "Nama penyedia",
    "site_url": "URL situs",
    "access_note": "Catatan akses",
    "manual_params": "Parameter",
}
_NAMA_JENIS = {MeasurementType.AUTOMATIC: "Otomatis", MeasurementType.MANUAL: "Manual"}


@dataclass(frozen=True)
class TypeFieldProblem:
    code: str
    field: str
    message: str


def type_field_problem(
    jenis: MeasurementType, isian: Mapping[str, Any], *, access_note_required: bool
) -> TypeFieldProblem | None:
    """Masalah pertama isian produk berjenis `jenis`, atau None kalau sesuai.

    `isian` adalah nilai akhir yang akan tersimpan; None berarti tidak diisi.
    Isian milik jenis lain dilaporkan lebih dulu, karena biasanya itu tanda
    jenis yang dipilih keliru. access_note_required berasal dari flag
    konfigurasi (Jira AC3, TBA klien).
    """
    for jenis_lain in MeasurementType:
        if jenis_lain == jenis:
            continue
        for field in TYPE_FIELDS[jenis_lain]:
            if isian.get(field) is not None:
                pesan = f"{_LABEL[field]} tidak boleh diisi untuk produk {_NAMA_JENIS[jenis]}."
                return TypeFieldProblem(FIELD_NOT_ALLOWED, field, pesan)
    wajib = _WAJIB[jenis]
    if jenis == MeasurementType.MANUAL and access_note_required:
        wajib = (*wajib, "access_note")
    for field in wajib:
        if isian.get(field) is None:
            pesan = f"{_LABEL[field]} wajib diisi untuk produk {_NAMA_JENIS[jenis]}."
            return TypeFieldProblem(FIELD_REQUIRED, field, pesan)
    return None
