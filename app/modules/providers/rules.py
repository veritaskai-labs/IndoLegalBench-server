"""Isian wajib dan terlarang per jenis pengukuran produk AI (PBI-23 AC1-AC3, SCRUM-201).

Aturan murni tanpa database. Service memanggilnya sebelum menyimpan, sehingga
isian yang tidak sesuai jenis menjadi 422 dengan field yang jelas. Constraint C1
di database (D1) tetap ada sebagai pengaman terakhir.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.modules.providers.models import MeasurementType

# Kode yang sama dengan SCRUM-200 (#79), supaya POST dan PATCH berbicara satu kontrak.
FIELD_REQUIRED = "FIELD_REQUIRED_FOR_TYPE"
FIELD_NOT_ALLOWED = "FIELD_NOT_ALLOWED_FOR_TYPE"
ACCESS_NOTE_SECRET = "ACCESS_NOTE_SECRET"  # pragma: allowlist secret (kode galat, bukan rahasia)
MODEL_PARAMS_RESERVED_KEY = "MODEL_PARAMS_RESERVED_KEY"
MODEL_PARAMS_SECRET = "MODEL_PARAMS_SECRET"  # pragma: allowlist secret (kode galat, bukan rahasia)
MODEL_PARAMS_TOO_LARGE = "MODEL_PARAMS_TOO_LARGE"

# SCRUM-247: parameter model adalah JSON bebas yang dikirim apa adanya ke penyedia.
# Model dan prompt diatur adaptor; stream membuat jawaban tidak bisa dibaca adaptor.
_KUNCI_SISTEM = frozenset({"model", "messages", "input", "stream"})
# Kunci yang namanya berarti kredensial. Dicocokkan utuh, supaya max_tokens tetap boleh.
_KUNCI_RAHASIA = re.compile(
    r"(x[-_])?(api[-_]?key|goog[-_]api[-_]key)|authorization|password|passwd|secret"
    r"|credential|(access|refresh|bearer|auth)[-_]?token",
    re.IGNORECASE,
)
MODEL_PARAMS_MAX_BYTES = 4096
MODEL_PARAMS_MAX_DEPTH = 5

# AC3: catatan akses bukan tempat kata sandi. Catatan ikut tercatat apa adanya di
# audit_logs yang tidak bisa dihapus, jadi pola "kata sandi: ..." ditolak sebelum
# tersimpan. Hanya penyaring kasar pendamping teks bantuan di form, bukan jaminan.
_POLA_RAHASIA = re.compile(
    r"\b(password|passwd|pwd|kata\s*sandi|sandi|pin|token|api[\s_-]?key)\s*[:=]",
    re.IGNORECASE,
)

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


def access_note_problem(catatan: str | None) -> TypeFieldProblem | None:
    """Masalah bila catatan akses tampak memuat kata sandi atau kunci (AC3), selain itu None."""
    if catatan is None or not _POLA_RAHASIA.search(catatan):
        return None
    pesan = "Catatan akses tidak boleh berisi kata sandi, PIN, token, atau API key."
    return TypeFieldProblem(ACCESS_NOTE_SECRET, "access_note", pesan)


def model_params_problem(params: Mapping[str, Any] | None) -> TypeFieldProblem | None:
    """Masalah pertama parameter model (SCRUM-247), atau None kalau boleh disimpan.

    Rahasia dicari sampai ke kunci bersarang, karena parameter tersimpan apa
    adanya dan tersalin ke audit log (L5).
    """
    if not params:
        return None
    for kunci in params:
        if kunci.lower() in _KUNCI_SISTEM:
            pesan = f"Parameter model tidak boleh mengatur '{kunci}', karena diatur sistem."
            return TypeFieldProblem(MODEL_PARAMS_RESERVED_KEY, "model_params", pesan)
    if any(_KUNCI_RAHASIA.fullmatch(kunci) for kunci in _semua_kunci(params)):
        pesan = "Parameter model tidak boleh berisi API key, token, atau kata sandi."
        return TypeFieldProblem(MODEL_PARAMS_SECRET, "model_params", pesan)
    ukuran = len(json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode())
    if ukuran > MODEL_PARAMS_MAX_BYTES or _kedalaman(params) > MODEL_PARAMS_MAX_DEPTH:
        pesan = (
            f"Parameter model maksimal {MODEL_PARAMS_MAX_BYTES} byte "
            f"dan {MODEL_PARAMS_MAX_DEPTH} tingkat bersarang."
        )
        return TypeFieldProblem(MODEL_PARAMS_TOO_LARGE, "model_params", pesan)
    return None


def _semua_kunci(nilai: Any):
    if isinstance(nilai, Mapping):
        for kunci, isi in nilai.items():
            yield kunci
            yield from _semua_kunci(isi)
    elif isinstance(nilai, list):
        for isi in nilai:
            yield from _semua_kunci(isi)


def _kedalaman(nilai: Any) -> int:
    """Jumlah tingkat object dan list; nilai tunggal 0."""
    if isinstance(nilai, Mapping):
        return 1 + max((_kedalaman(isi) for isi in nilai.values()), default=0)
    if isinstance(nilai, list):
        return 1 + max((_kedalaman(isi) for isi in nilai), default=0)
    return 0
