"""Logika bisnis modul cases.

PBI-3 Menulis kasus hukum terstruktur.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

TODO(PBI-3): implementasikan sesuai acceptance criteria. Dua fungsi di
bawah adalah pintu yang dipakai suites (SCRUM-99) untuk jumlah kasus dan
larangan hapus. Keduanya mengembalikan "belum ada kasus" sampai tabel
cases ada, supaya modul suites tidak mengimpor tabel cases.
"""

import uuid

from sqlalchemy.orm import Session


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Jumlah kasus di dalam satu suite. PBI-3 yang mengisi angkanya."""
    del db, suite_id
    return 0


def has_approved_case(db: Session, suite_id: uuid.UUID) -> bool:
    """True kalau suite berisi kasus berstatus approved. PBI-3."""
    del db, suite_id
    return False
