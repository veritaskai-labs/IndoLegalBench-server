"""Audit laporan perbandingan. PBI-15 SCRUM-300, PBI-17 AC9, PBI-18 AC1.

Laporan dibuat, diterbitkan, dan digantikan adalah aksi pengguna yang perlu jejak.
Tabel anak (sesi, hasil, dasar kasus, versi terbit) hanya INSERT dan diwakili event
laporannya (tests/modules/audit/test_audit_catalog_guard.py). Di halaman audit objeknya
tampil sebagai judul laporan, bukan UUID.

Isolasi: tracker diuji dengan SimpleNamespace (tanpa DB); jalur listener dan label
memakai db_session SQLite baru per tes.

Label: Positif = tercatat, Negatif = tidak boleh tercatat.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.schemas import AuditLogFilter
from app.modules.audit.trackers import Change, tracker_for
from app.modules.reports import service as reports_service
from app.modules.reports.models import ReportStatus, ScoreScope
from app.shared.security import Role
from tests.reports_factory import (
    buat_admin,
    buat_rubrik,
    buat_sesi,
    dasar,
    hasil,
    laporan,
    sesi_laporan,
)

LAPORAN_ID = uuid.UUID("00000000-0000-0000-0000-000000000300")


def _baris(**kolom) -> SimpleNamespace:
    isi = {
        "id": LAPORAN_ID,
        "title": "Laporan Oktober",
        "score_scope": ScoreScope.TEST,
        "has_dev_scores": False,
        "measurement_filter": "all",
        "product_label_mode": "real_name",
        "language": "en",
        "rubric_version_id": uuid.UUID("00000000-0000-0000-0000-000000000222"),
        "case_basis_count": 12,
        "status": ReportStatus.SAVED,
    } | kolom
    return SimpleNamespace(**isi)


def test_laporan_baru_tercatat():
    """Positif: report.created berisi judul, jenis nilai, penanda dev, dan jumlah kasus dasar."""
    tracker = tracker_for("reports")

    (event,) = tracker.created(_baris())

    assert tracker.entity_type == AuditEntityType.REPORT
    assert (event.action, event.entity_id) == ("report.created", LAPORAN_ID)
    assert event.after == {
        "title": "Laporan Oktober",
        "score_scope": "test",
        "has_dev_scores": False,
        "measurement_filter": "all",
        "product_label_mode": "real_name",
        "language": "en",
        "rubric_version_id": "00000000-0000-0000-0000-000000000222",
        "case_basis_count": 12,
    }


@pytest.mark.parametrize(
    ("lama", "baru", "aksi"),
    [
        (ReportStatus.SAVED, ReportStatus.PUBLISHED, "report.published"),
        (ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED, "report.superseded"),
    ],
)
def test_terbit_dan_digantikan_tercatat(lama, baru, aksi):
    """Positif: PBI-17 AC9, siapa menerbitkan dan kapan versi lama digantikan bisa ditelusuri."""
    (event,) = tracker_for("reports").updated(
        _baris(status=baru), {"status": Change(old=lama, new=baru)}
    )

    assert event.action == aksi
    assert (event.before, event.after) == ({"status": lama.value}, {"status": baru.value})


def test_perubahan_kolom_lain_tidak_dipantau():
    """Negatif: kolom selain status beku (trigger menolaknya); listener hanya membaca fields."""
    tracker = tracker_for("reports")

    assert tracker.fields == ("status",)
    assert tracker.updated(_baris(), {}) == []


# --- Lewat listener dan halaman audit -------------------------------------------------


@pytest.fixture
def laporan_tersimpan(db_session):
    admin = buat_admin(db_session)
    rubrik = buat_rubrik(db_session, admin)
    sesi = buat_sesi(db_session, admin, "Produk audit 300")
    report = laporan(admin, rubrik, case_basis_count=1)
    reports_service.save_report(
        db_session, report, [sesi_laporan(report, sesi)], [hasil(report, sesi)], [dasar(report)]
    )
    db_session.commit()
    return db_session, admin, report


def test_menyimpan_laporan_menulis_satu_catatan(laporan_tersimpan):
    """Positif: satu report.created, tanpa catatan terpisah untuk baris anaknya."""
    db, _, report = laporan_tersimpan

    rows = db.scalars(select(AuditLog).where(AuditLog.entity_type == "report")).all()

    assert [(row.action, row.entity_id) for row in rows] == [("report.created", report.id)]


def test_halaman_audit_menampilkan_judul_laporan(laporan_tersimpan):
    """Positif: PBI-18 F-06, objeknya terbaca sebagai judul laporan, bukan UUID."""
    db, admin, _ = laporan_tersimpan

    halaman = audit_service.search_logs(
        db,
        AuditLogFilter(entity_type=AuditEntityType.REPORT),
        viewer_id=admin.id,
        viewer_role=Role.ADMIN,
    )

    assert [item.entity_label for item in halaman.items] == ["Laporan Oktober"]
