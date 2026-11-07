"""Penjaga katalog audit D6a. PBI-18 AC1, bug SCRUM-173.

Listener audit hanya mencatat tabel yang terdaftar di TRACKERS. Tabel baru
tidak ikut tercatat dengan sendirinya: suite_snapshots (PBI-8) lolos tanpa
audit karena tidak ada yang mendaftarkannya. Test ini memaksa setiap tabel
model punya tracker, atau tercantum di PENGECUALIAN beserta alasannya.

Model dimuat dari setiap app/modules/*/models.py, bukan lewat app.main. Modul yang
belum punya router (grading di SCRUM-222) tidak diimpor app.main, sehingga tabelnya
dulu tidak terlihat oleh penjaga ini dan lolos tanpa tracker maupun alasan.
"""

import importlib
import importlib.util
import pkgutil

import app.modules
from app.modules.audit.trackers import tracker_for
from app.shared.database import Base


def _muat_semua_model() -> None:
    """Impor models.py setiap modul supaya Base.metadata memuat semua tabel."""
    for modul in pkgutil.iter_modules(app.modules.__path__):
        nama = f"app.modules.{modul.name}.models"
        if importlib.util.find_spec(nama) is not None:
            importlib.import_module(nama)


_muat_semua_model()

# Tabel yang sengaja tidak dicatat. Tambah di sini hanya dengan alasan.
PENGECUALIAN = {
    "audit_logs": "tabel audit itu sendiri",
    "sessions": "sesi login; D6 pertanyaan 11: login dicatat dari log Zitadel",
    "suite_snapshot_items": "isi snapshot; sudah diwakili event suite.snapshot_created",
    "regulation_types": "daftar referensi; hanya berubah lewat migrasi atau is_active",
    "legal_refs": "SCRUM-159: hanya migrasi data yang menulis; tracker ditentukan di SCRUM-160",
    "legal_ref_links": "SCRUM-159: hanya migrasi data yang menulis; tracker ditentukan di SCRUM-160",
    "notifications": "kotak masuk in-app; penugasan dan verdict yang memicunya sudah dicatat di tabel review",
    "measurement_session_suites": "SCRUM-205: pilihan suite, ditulis sekali bersama sesinya",
    "session_product_copies": "SCRUM-205: salinan sekali tulis; perubahan produk sendiri sudah dicatat di ai_products",
    "case_snapshots": "SCRUM-205: hanya INSERT, UPDATE dan DELETE ditolak trigger",
    "case_snapshot_items": "SCRUM-205: hanya INSERT; isinya salinan versi kasus yang sudah dicatat",
    "attempts": "SCRUM-205: jawaban mentah permanen (trigger); bukti panggilan di call_logs (SCRUM-209)",
    "rubric_aspects": "SCRUM-222: hanya INSERT (trigger), ditulis bersama versinya",
    "rubric_levels": "SCRUM-222: hanya INSERT (trigger), ditulis bersama versinya",
    "rubric_level_descriptors": "SCRUM-222: hanya INSERT (trigger), ditulis bersama versinya",
    "grading_runs": "SCRUM-222: belum ada penulis; tracker ditentukan SCRUM-226 (tombol Nilai)",
    "grading_results": "SCRUM-222: hasil mesin, hanya INSERT (trigger), bukan aksi pengguna",
    "grading_result_reasons": "SCRUM-222: tautan hasil mesin, hanya INSERT (trigger)",
    "reason_codes": "SCRUM-222: hanya migrasi yang menulis (seed usulan); tracker saat ada API",
    "llm_judge_results": "SCRUM-222: hasil mesin, hanya INSERT (trigger); biaya ada di baris ini",
    "llm_criterion_results": "SCRUM-222: hasil mesin, hanya INSERT (trigger)",
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
