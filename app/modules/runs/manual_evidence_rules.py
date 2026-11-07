"""Aturan murni bukti jawaban manual per D9. PBI-22 (SCRUM-176) AC7; SCRUM-233.

Bukti opsional berupa berkas tangkapan layar atau tautan, maksimal 3 per jawaban
(attempt_evidence, D9). Berkas hanya PNG, JPG, atau PDF; tipenya dibaca dari isi berkas
(magic bytes), bukan dipercaya dari ekstensi atau Content-Type browser (tiket AC7).
Ukuran bebas dengan batas teknis dari tim, diisi lewat MANUAL_EVIDENCE_MAX_BYTES.

Siapa yang boleh melihat bukti masih TBA (D9 [PENDING]): default semua yang bisa membuka
sesi manual, dipersempit ke Admin lewat MANUAL_EVIDENCE_VISIBILITY=admin_only.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from app.shared.exceptions import ConflictError, PayloadTooLargeError, ValidationError
from app.shared.security import Role

MAX_EVIDENCE_PER_ATTEMPT = 3
# D9: url varchar(2048).
MAX_LINK_LENGTH = 2048
# Kolom file_name varchar(255) [USULAN SA].
MAX_FILE_NAME_LENGTH = 255
_DEFAULT_FILE_NAME = "bukti"

EvidenceVisibility = Literal["session_viewers", "admin_only"]

# D9 mime_type: tiga tipe yang diizinkan, sama dengan CHECK ck_attempt_evidence_mime_type.
MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"
MIME_PDF = "application/pdf"

# Signature resmi tiap format; cukup beberapa byte pertama.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", MIME_PNG),
    (b"\xff\xd8\xff", MIME_JPEG),
    (b"%PDF-", MIME_PDF),
)
# Content-Type yang dikirim browser untuk tiap tipe. image/jpg dipakai browser lama.
_DECLARED = {
    MIME_PNG: MIME_PNG,
    MIME_JPEG: MIME_JPEG,
    "image/jpg": MIME_JPEG,
    MIME_PDF: MIME_PDF,
}
# D10: Admin dan Kontributor bisa membuka sesi manual milik siapa pun.
_SESSION_VIEWERS = frozenset({Role.ADMIN, Role.CONTRIBUTOR})


def detect_file_type(head: bytes, *, declared: str | None) -> str:
    """Tipe MIME dari isi berkas. Ditolak bila bukan PNG/JPG/PDF atau tidak cocok label."""
    actual = next((mime for sig, mime in _SIGNATURES if head.startswith(sig)), None)
    if actual is None:
        raise ValidationError(
            "Bukti hanya boleh berkas PNG, JPG, atau PDF.",
            code="EVIDENCE_TYPE_NOT_ALLOWED",
            field="file",
        )
    if _DECLARED.get((declared or "").split(";")[0].strip().lower()) != actual:
        raise ValidationError(
            "Isi berkas tidak sesuai dengan tipenya.",
            code="EVIDENCE_TYPE_MISMATCH",
            field="file",
        )
    return actual


def ensure_size(size: int, *, limit_bytes: int) -> None:
    if size <= 0:
        raise ValidationError("Berkas bukti kosong.", code="EVIDENCE_EMPTY", field="file")
    if size > limit_bytes:
        # 413, sama dengan penolakan dari Content-Length di router (review #121).
        raise PayloadTooLargeError(
            "Berkas bukti melebihi batas ukuran.",
            code="EVIDENCE_TOO_LARGE",
            field="file",
            details={"limit_bytes": limit_bytes},
        )


def ensure_room(existing: int) -> None:
    """D9: maksimal 3 bukti per jawaban. Trigger PostgreSQL menjaga hal yang sama."""
    if existing >= MAX_EVIDENCE_PER_ATTEMPT:
        raise ConflictError(
            f"Satu jawaban paling banyak {MAX_EVIDENCE_PER_ATTEMPT} bukti.",
            code="EVIDENCE_LIMIT_REACHED",
        )


def normalize_link(url: str) -> str:
    """Hanya http(s) dengan host, jadi javascript: dan sejenisnya tidak pernah tersimpan."""
    value = (url or "").strip()
    parts = urlsplit(value)
    if len(value) > MAX_LINK_LENGTH or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError(
            "Tautan bukti harus alamat http atau https yang lengkap.",
            code="EVIDENCE_LINK_INVALID",
            field="url",
        )
    return value


def can_view_evidence(role: Role | str, *, policy: EvidenceVisibility) -> bool:
    role = Role(role)
    if policy == "admin_only":
        return role is Role.ADMIN
    return role in _SESSION_VIEWERS


def safe_file_name(name: str | None) -> str:
    """Nama asli berkas tanpa jalur folder, karakter kontrol, atau tanda kutip.

    Dipakai di header Content-Disposition saat unduh, jadi CR/LF dan kutip yang bisa
    memecah header dibuang. Nama yang kepanjangan dipotong dengan ekstensi tetap ada.
    """
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in base if ch.isprintable() and ch != '"').strip()
    if not base:
        return _DEFAULT_FILE_NAME
    if len(base) <= MAX_FILE_NAME_LENGTH:
        return base
    stem, dot, ext = base.rpartition(".")
    if dot and stem and len(ext) < 16:
        return stem[: MAX_FILE_NAME_LENGTH - len(ext) - 1] + "." + ext
    return base[:MAX_FILE_NAME_LENGTH]
