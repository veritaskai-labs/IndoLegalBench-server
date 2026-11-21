"""Zona waktu WIB untuk modul reports.

Bulan titik tren (D4 [KEPUTUSAN SA]), rentang tren, dan tanggal di PDF memakai WIB.
Satu konstanta supaya tren dan PDF tidak bisa memakai zona yang berbeda.
"""

from datetime import timedelta, timezone

WIB = timezone(timedelta(hours=7), "WIB")
