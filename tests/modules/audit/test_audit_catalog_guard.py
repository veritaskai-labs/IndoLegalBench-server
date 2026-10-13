"""Penjaga katalog audit D6a. PBI-18 AC1, bug SCRUM-173.

Listener audit hanya mencatat tabel yang terdaftar di TRACKERS. Tabel baru
tidak ikut tercatat dengan sendirinya: suite_snapshots (PBI-8) lolos tanpa
audit karena tidak ada yang mendaftarkannya. Test ini memaksa setiap tabel
model punya tracker, atau tercantum di PENGECUALIAN beserta alasannya.
"""

import app.main  # noqa: F401  memuat semua model supaya metadata lengkap
from app.modules.audit.trackers import tracker_for
from app.shared.database import Base

# Tabel yang sengaja tidak dicatat. Tambah di sini hanya dengan alasan.
PENGECUALIAN = {
    "audit_logs": "tabel audit itu sendiri",
    "sessions": "sesi login; D6 pertanyaan 11: login dicatat dari log Zitadel",
    "suite_snapshot_items": "isi snapshot; sudah diwakili event suite.snapshot_created",
}


def test_setiap_tabel_dicatat_atau_dikecualikan_dengan_alasan():
    # Arrange
    tabel = set(Base.metadata.tables)

    # Act
    lolos = sorted(nama for nama in tabel if tracker_for(nama) is None and nama not in PENGECUALIAN)

    # Assert
    assert lolos == [], (
        f"Tabel {lolos} tidak tercatat di audit. Daftarkan tracker-nya di "
        "app/modules/audit/trackers.py (TRACKERS), atau tambahkan ke PENGECUALIAN "
        "di test ini beserta alasannya."
    )


def test_pengecualian_tidak_basi():
    # Arrange
    tabel = set(Base.metadata.tables)

    # Act
    basi = sorted(nama for nama in PENGECUALIAN if nama not in tabel or tracker_for(nama))

    # Assert
    assert basi == [], (
        f"Pengecualian {basi} sudah tidak ada atau ternyata dilacak. Hapus dari daftar."
    )
