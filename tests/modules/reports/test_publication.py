"""Penerbitan dan daftar laporan terbit. PBI-17 SCRUM-84 SCRUM-332.

AC9, AC10, AC12. D1: hanya laporan perbandingan yang diterbitkan, versi baru menunjuk
versi lama, nama produk yang keluar adalah label beku.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.modules.audit.models import AuditLog
from app.modules.reports import publication, repository
from app.modules.reports.models import (
    ProductLabelMode,
    PublishedReport,
    Report,
    ReportStatus,
    ScoreScope,
)
from app.modules.reports.service import REPORT_HAS_DEV_SCORES
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.pagination import PageParams
from tests import reports_factory as pabrik

PEMILIK_LAIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _simpan(db, admin, rubrik, sesi, **ubah_laporan):
    """Satu laporan test tersimpan, dengan label yang dibekukan di sesinya."""
    label = ubah_laporan.pop("product_label", "Gemini")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1, **ubah_laporan)
    repository.add_report(
        db,
        report,
        [pabrik.sesi_laporan(report, sesi, product_label=label)],
        [pabrik.hasil(report, sesi)],
        [pabrik.dasar(report)],
    )
    db.commit()
    return report


def _siap(db):
    admin = pabrik.buat_admin(db)
    rubrik = pabrik.buat_rubrik(db, admin)
    sesi = pabrik.buat_sesi(db, admin, "Gemini")
    return admin, rubrik, sesi


def _jumlah_terbit(db) -> int:
    return db.scalar(select(func.count()).select_from(PublishedReport)) or 0


class TestPublish:
    def test_admin_publishes_a_saved_test_report_as_version_one(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        report = _simpan(db_session, admin, rubrik, sesi, created_by=PEMILIK_LAIN)

        # Act
        hasil = publication.publish(
            db_session, report_id=report.id, actor_id=admin.id, supersedes_id=None
        )

        # Assert
        assert (hasil.report_id, hasil.version_no, hasil.supersedes_id) == (report.id, 1, None)
        tersimpan = db_session.get(Report, report.id)
        assert tersimpan.status == ReportStatus.PUBLISHED
        baris = db_session.scalar(
            select(PublishedReport).where(PublishedReport.report_id == report.id)
        )
        assert baris is not None
        assert (baris.published_by, baris.version_no) == (admin.id, 1)
        assert (tersimpan.created_by, tersimpan.title) == (PEMILIK_LAIN, "Laporan Oktober")

    def test_a_second_saved_report_can_also_stay_published(self, db_session):
        # Arrange: tidak ada satu laporan terbit global. D1 tidak menggabungkan mereka.
        admin, rubrik, sesi = _siap(db_session)
        pertama = _simpan(db_session, admin, rubrik, sesi)
        kedua = _simpan(db_session, admin, rubrik, sesi, title="Laporan lain")

        # Act
        publication.publish(db_session, report_id=pertama.id, actor_id=admin.id)
        publication.publish(db_session, report_id=kedua.id, actor_id=admin.id)

        # Assert
        assert db_session.get(Report, pertama.id).status == ReportStatus.PUBLISHED
        assert db_session.get(Report, kedua.id).status == ReportStatus.PUBLISHED
        assert _jumlah_terbit(db_session) == 2

    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_a_report_with_dev_scores_is_rejected_and_not_stored(self, db_session, scope):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        report = _simpan(
            db_session,
            admin,
            rubrik,
            sesi,
            score_scope=scope,
            has_dev_scores=True,
        )

        # Act
        with pytest.raises(ConflictError) as galat:
            publication.publish(db_session, report_id=report.id, actor_id=admin.id)

        # Assert
        assert galat.value.code == REPORT_HAS_DEV_SCORES
        assert db_session.get(Report, report.id).status == ReportStatus.SAVED
        assert _jumlah_terbit(db_session) == 0

    def test_a_missing_report_is_not_found(self, db_session):
        # Arrange
        admin, _, _ = _siap(db_session)

        # Act
        with pytest.raises(NotFoundError) as galat:
            publication.publish(db_session, report_id=uuid.uuid4(), actor_id=admin.id)

        # Assert
        assert galat.value.code == publication.REPORT_NOT_FOUND

    @pytest.mark.parametrize("status", [ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED])
    def test_only_a_saved_report_can_be_published(self, db_session, status):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        report = _simpan(db_session, admin, rubrik, sesi, status=status)

        # Act
        with pytest.raises(ConflictError) as galat:
            publication.publish(db_session, report_id=report.id, actor_id=admin.id)

        # Assert
        assert galat.value.code == publication.REPORT_NOT_PUBLISHABLE
        assert galat.value.code != REPORT_HAS_DEV_SCORES
        assert _jumlah_terbit(db_session) == 0


class TestReplace:
    def test_a_new_version_keeps_the_old_row_and_marks_it_superseded(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        lama = _simpan(db_session, admin, rubrik, sesi, title="Laporan salah")
        baru = _simpan(db_session, admin, rubrik, sesi, title="Laporan perbaikan")
        versi_lama = publication.publish(db_session, report_id=lama.id, actor_id=admin.id)

        # Act
        versi_baru = publication.publish(
            db_session,
            report_id=baru.id,
            actor_id=admin.id,
            supersedes_id=versi_lama.id,
        )

        # Assert
        assert (versi_baru.version_no, versi_baru.supersedes_id) == (2, versi_lama.id)
        assert db_session.get(Report, lama.id).status == ReportStatus.SUPERSEDED
        assert db_session.get(Report, baru.id).status == ReportStatus.PUBLISHED
        assert _jumlah_terbit(db_session) == 2
        assert db_session.get(PublishedReport, versi_lama.id) is not None

    def test_a_third_version_continues_the_chain(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        pertama = _simpan(db_session, admin, rubrik, sesi, title="v1")
        kedua = _simpan(db_session, admin, rubrik, sesi, title="v2")
        ketiga = _simpan(db_session, admin, rubrik, sesi, title="v3")
        v1 = publication.publish(db_session, report_id=pertama.id, actor_id=admin.id)
        v2 = publication.publish(
            db_session, report_id=kedua.id, actor_id=admin.id, supersedes_id=v1.id
        )

        # Act
        v3 = publication.publish(
            db_session, report_id=ketiga.id, actor_id=admin.id, supersedes_id=v2.id
        )

        # Assert
        assert v3.version_no == 3
        assert db_session.get(Report, kedua.id).status == ReportStatus.SUPERSEDED
        assert db_session.get(Report, ketiga.id).status == ReportStatus.PUBLISHED

    def test_an_unknown_publication_cannot_be_replaced(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        baru = _simpan(db_session, admin, rubrik, sesi)

        # Act
        with pytest.raises(NotFoundError) as galat:
            publication.publish(
                db_session,
                report_id=baru.id,
                actor_id=admin.id,
                supersedes_id=uuid.uuid4(),
            )

        # Assert
        assert galat.value.code == publication.PUBLICATION_NOT_FOUND
        assert db_session.get(Report, baru.id).status == ReportStatus.SAVED
        assert _jumlah_terbit(db_session) == 0

    def test_only_the_current_published_version_can_be_replaced(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        pertama = _simpan(db_session, admin, rubrik, sesi, title="v1")
        kedua = _simpan(db_session, admin, rubrik, sesi, title="v2")
        ketiga = _simpan(db_session, admin, rubrik, sesi, title="v3")
        v1 = publication.publish(db_session, report_id=pertama.id, actor_id=admin.id)
        publication.publish(db_session, report_id=kedua.id, actor_id=admin.id, supersedes_id=v1.id)

        # Act
        with pytest.raises(ConflictError) as galat:
            publication.publish(
                db_session,
                report_id=ketiga.id,
                actor_id=admin.id,
                supersedes_id=v1.id,
            )

        # Assert
        assert galat.value.code == publication.REPORT_NOT_PUBLISHABLE
        assert db_session.get(Report, ketiga.id).status == ReportStatus.SAVED


class TestPublishedList:
    def test_the_list_is_only_the_current_published_reports(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        tersimpan = _simpan(db_session, admin, rubrik, sesi, title="Masih draf")
        lama = _simpan(db_session, admin, rubrik, sesi, title="Versi lama")
        baru = _simpan(db_session, admin, rubrik, sesi, title="Versi baru")
        v1 = publication.publish(db_session, report_id=lama.id, actor_id=admin.id)
        publication.publish(db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id)

        # Act
        halaman = publication.list_published(db_session, PageParams(page=1, size=20))

        # Assert
        assert [item.title for item in halaman.items] == ["Versi baru"]
        assert tersimpan.title == "Masih draf"
        assert halaman.total == 1

    def test_labels_are_the_frozen_ones_and_hide_the_live_product(self, db_session):
        # Arrange: produk hidupnya Gemini, label yang dipilih Admin saat membuat adalah kode.
        admin, rubrik, sesi = _siap(db_session)
        lain = pabrik.buat_sesi(db_session, admin, "DeepSeek")
        report = pabrik.laporan(
            admin,
            rubrik,
            case_basis_count=1,
            product_label_mode=ProductLabelMode.CODE,
        )
        repository.add_report(
            db_session,
            report,
            [
                pabrik.sesi_laporan(report, sesi, product_label="Product B"),
                pabrik.sesi_laporan(report, lain, product_label="Product A"),
            ],
            [pabrik.hasil(report, sesi), pabrik.hasil(report, lain, rank=2)],
            [pabrik.dasar(report)],
        )
        db_session.commit()
        publication.publish(db_session, report_id=report.id, actor_id=admin.id)

        # Act
        halaman = publication.list_published(db_session, PageParams())

        # Assert
        item = halaman.items[0]
        assert item.product_labels == ["Product A", "Product B"]
        assert "Gemini" not in item.product_labels
        assert "DeepSeek" not in item.product_labels
        assert not hasattr(item, "product_id")
        assert not hasattr(item, "session_id")

    def test_the_list_is_paged_like_the_saved_report_list(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        pertama = _simpan(db_session, admin, rubrik, sesi, title="Satu")
        kedua = _simpan(db_session, admin, rubrik, sesi, title="Dua")
        publication.publish(db_session, report_id=pertama.id, actor_id=admin.id)
        publication.publish(db_session, report_id=kedua.id, actor_id=admin.id)

        # Act
        halaman = publication.list_published(db_session, PageParams(page=1, size=1))

        # Assert
        assert (halaman.total, halaman.page, halaman.size, len(halaman.items)) == (2, 1, 1, 1)


class TestAudit:
    def test_publish_and_replace_are_recorded_on_the_report(self, db_session):
        # Arrange
        admin, rubrik, sesi = _siap(db_session)
        lama = _simpan(db_session, admin, rubrik, sesi, title="Lama")
        baru = _simpan(db_session, admin, rubrik, sesi, title="Baru")
        v1 = publication.publish(db_session, report_id=lama.id, actor_id=admin.id)

        # Act
        publication.publish(db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id)
        baris = set(
            db_session.execute(
                select(AuditLog.action, AuditLog.entity_id).where(
                    AuditLog.entity_type == "report",
                    AuditLog.action.in_(("report.published", "report.superseded")),
                )
            ).all()
        )

        # Assert
        assert baris == {
            ("report.published", lama.id),
            ("report.published", baru.id),
            ("report.superseded", lama.id),
        }
