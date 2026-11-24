"""Aturan ganti versi laporan terbit: hanya laporan dengan himpunan produk yang sama.

PBI-17 AC9, SCRUM-371 (feedback UAT klien Sprint 4). Laporan A vs B hanya boleh mengganti
atau digantikan laporan A vs B; urutan produk tidak berpengaruh.

Produk dibandingkan lewat product_id di report_sessions, bukan label beku: label bisa
berupa kode ("Product A") yang artinya berbeda di tiap laporan (mode kode PBI-15 AC9).

Modul ini murni: tidak menyentuh database dan tidak mengenal HTTP. Aturan yang sama
dipakai untuk daftar kandidat dan untuk menolak penerbitan, supaya keduanya tidak
pelan-pelan berbeda.
"""

import uuid
from collections.abc import Iterable

REPORT_PRODUCTS_MISMATCH = "REPORT_PRODUCTS_MISMATCH"


def same_product_set(first: Iterable[uuid.UUID], second: Iterable[uuid.UUID]) -> bool:
    """Apakah dua laporan membandingkan himpunan produk yang persis sama."""
    raise NotImplementedError


def ensure_same_products(replacement: Iterable[uuid.UUID], replaced: Iterable[uuid.UUID]) -> None:
    """Tolak (409) penggantian oleh laporan yang membandingkan produk lain."""
    raise NotImplementedError
