"""Ganti versi terbit hanya dengan laporan yang produknya sama. PBI-17 AC9, SCRUM-371.

Lewat service publication.publish di SQLite (fixture db_session). Kedua jalur FE (terbitkan
dengan "Ganti versi yang sedang terbit", dan "Ganti versi" dari laporan terbit) memanggil
POST publish yang sama, jadi penjagaan di sini berlaku untuk keduanya. Penguncian baris yang
sebenarnya hanya bisa dibuktikan di PostgreSQL (tests/integration/test_replacement_pg.py);
di sini urutan panggilannya dibuktikan dengan monkeypatch.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.modules.reports import publication, repository
from app.modules.reports.models import (
    ProductLabelMode,
    PublishedReport,
    Report,
    ReportStatus,
    ScoreScope,
)
from app.modules.reports.replacement import REPORT_PRODUCTS_MISMATCH
from app.modules.reports.service import REPORT_HAS_DEV_SCORES
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.pagination import PageParams
from tests import replacement_world as dunia
from tests import reports_factory as pabrik


@pytest.fixture
def produk(db_session):
    """Admin, rubrik, dan satu sesi untuk tiap produk A, B, C."""
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = {nama: pabrik.buat_sesi(db_session, admin, f"Produk {nama}") for nama in "ABC"}
    return admin, rubrik, sesi


def _terbit_ab(db, admin, rubrik, sesi):
    """Versi 1 yang sedang terbit: laporan A vs B."""
    lama = dunia.simpan(db, admin, rubrik, [sesi["A"], sesi["B"]], title="A vs B v1")
    return lama, publication.publish(db, report_id=lama.id, actor_id=admin.id)


def _jumlah_terbit(db) -> int:
    return db.scalar(select(func.count()).select_from(PublishedReport)) or 0


class TestProductIds:
    def test_the_products_of_a_report_are_read_from_its_frozen_sessions(self, db_session, produk):
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["B"], sesi["A"]])

        assert repository.product_ids(db_session, report.id) == frozenset(
            {sesi["A"].product_id, sesi["B"].product_id}
        )

    def test_an_unknown_report_has_no_products(self, db_session, produk):
        assert repository.product_ids(db_session, uuid.uuid4()) == frozenset()


class TestReplaceWithTheSameProducts:
    def test_the_same_products_in_another_order_and_newer_sessions_replace_the_version(
        self, db_session, produk
    ):
        # Arrange: B vs A dari sesi bulan berikutnya mengganti A vs B
        admin, rubrik, sesi = produk
        lama, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        baru = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [
                dunia.sesi_baru(db_session, admin, sesi["B"]),
                dunia.sesi_baru(db_session, admin, sesi["A"]),
            ],
            title="B vs A v2",
        )

        # Act
        v2 = publication.publish(
            db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id
        )

        # Assert: versi lama tetap tersimpan (AC9)
        assert (v2.version_no, v2.supersedes_id) == (2, v1.id)
        assert db_session.get(Report, lama.id).status == ReportStatus.SUPERSEDED
        assert db_session.get(PublishedReport, v1.id) is not None

    def test_a_code_label_report_can_replace_a_real_name_report_of_the_same_products(
        self, db_session, produk
    ):
        # Label beku berbeda ("Product A" vs nama asli); yang dibandingkan product_id.
        admin, rubrik, sesi = produk
        _, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        baru = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["A"], sesi["B"]],
            labels=["Product A", "Product B"],
            product_label_mode=ProductLabelMode.CODE,
        )

        v2 = publication.publish(
            db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id
        )

        assert v2.version_no == 2

    def test_publishing_without_a_replaced_version_never_reads_products(
        self, db_session, produk, monkeypatch
    ):
        # Terbit biasa tidak punya pembanding: tidak ada query produk tambahan.
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])

        def jangan_dipanggil(*_args):
            raise AssertionError("product_ids tidak boleh dipanggil tanpa supersedes_id")

        monkeypatch.setattr(repository, "product_ids", jangan_dipanggil)

        assert (
            publication.publish(db_session, report_id=report.id, actor_id=admin.id).version_no == 1
        )


class TestReplaceWithOtherProducts:
    @pytest.mark.parametrize(
        "pengganti",
        [("A", "B", "C"), ("A",), ("A", "C"), ("C",)],
        ids=["superset", "subset", "one-other-product", "disjoint"],
    )
    def test_other_products_are_refused_and_nothing_is_written(self, db_session, produk, pengganti):
        # Arrange
        admin, rubrik, sesi = produk
        lama, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        baru = dunia.simpan(db_session, admin, rubrik, [sesi[nama] for nama in pengganti])

        # Act
        with pytest.raises(ConflictError) as galat:
            publication.publish(
                db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id
            )

        # Assert: 409 dengan alasan, rantai versi utuh, tidak ada perubahan parsial (AC9)
        assert galat.value.code == REPORT_PRODUCTS_MISMATCH
        db_session.expire_all()
        assert db_session.get(Report, baru.id).status == ReportStatus.SAVED
        assert db_session.get(Report, lama.id).status == ReportStatus.PUBLISHED
        assert _jumlah_terbit(db_session) == 1
        assert (
            publication.list_published(db_session, PageParams(page=1, size=20)).items[0].id == v1.id
        )

    def test_after_a_refusal_the_right_report_can_still_replace_the_version(
        self, db_session, produk
    ):
        admin, rubrik, sesi = produk
        _, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        salah = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])
        benar = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        with pytest.raises(ConflictError):
            publication.publish(
                db_session, report_id=salah.id, actor_id=admin.id, supersedes_id=v1.id
            )

        v2 = publication.publish(
            db_session, report_id=benar.id, actor_id=admin.id, supersedes_id=v1.id
        )

        assert (v2.version_no, v2.supersedes_id) == (2, v1.id)


class TestOrderOfChecks:
    """Galat yang sudah ada tetap menang; cek produk paling akhir, setelah kedua kunci."""

    def test_an_unknown_replaced_version_is_still_404(self, db_session, produk):
        admin, rubrik, sesi = produk
        baru = dunia.simpan(db_session, admin, rubrik, [sesi["C"]])

        with pytest.raises(NotFoundError) as galat:
            publication.publish(
                db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=uuid.uuid4()
            )

        assert galat.value.code == publication.PUBLICATION_NOT_FOUND

    def test_a_superseded_version_with_other_products_is_still_not_publishable(
        self, db_session, produk
    ):
        admin, rubrik, sesi = produk
        _, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        v2_laporan = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        publication.publish(
            db_session, report_id=v2_laporan.id, actor_id=admin.id, supersedes_id=v1.id
        )
        lain = dunia.simpan(db_session, admin, rubrik, [sesi["C"]])

        with pytest.raises(ConflictError) as galat:
            publication.publish(
                db_session, report_id=lain.id, actor_id=admin.id, supersedes_id=v1.id
            )

        assert galat.value.code == publication.REPORT_NOT_PUBLISHABLE

    def test_a_dev_report_with_other_products_is_still_rejected_for_dev_scores(
        self, db_session, produk
    ):
        admin, rubrik, sesi = produk
        _, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        dev = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["C"]],
            score_scope=ScoreScope.DEV,
            has_dev_scores=True,
        )

        with pytest.raises(ConflictError) as galat:
            publication.publish(
                db_session, report_id=dev.id, actor_id=admin.id, supersedes_id=v1.id
            )

        assert galat.value.code == REPORT_HAS_DEV_SCORES

    def test_products_are_compared_only_after_both_reports_are_locked(
        self, db_session, produk, monkeypatch
    ):
        # Arrange: catat urutan panggilan repository (monkeypatch membungkus fungsi aslinya)
        admin, rubrik, sesi = produk
        lama, v1 = _terbit_ab(db_session, admin, rubrik, sesi)
        baru = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        urutan: list[tuple[str, uuid.UUID]] = []
        for nama in ("lock_report", "lock_publication", "product_ids"):
            asli = getattr(repository, nama)

            def catat(db, target_id, _nama=nama, _asli=asli):
                urutan.append((_nama, target_id))
                return _asli(db, target_id)

            monkeypatch.setattr(repository, nama, catat)

        # Act
        publication.publish(db_session, report_id=baru.id, actor_id=admin.id, supersedes_id=v1.id)

        # Assert: kedua laporan dikunci dulu, baru produk keduanya dibaca
        assert urutan == [
            ("lock_report", baru.id),
            ("lock_publication", v1.id),
            ("lock_report", lama.id),
            ("product_ids", baru.id),
            ("product_ids", lama.id),
        ]


class TestOtherIntegrityErrors:
    def test_an_integrity_error_that_is_not_a_taken_version_is_raised_and_rolled_back(
        self, db_session, produk, monkeypatch
    ):
        # Perilaku lama #151 tanpa tes: hanya unique versi yang jadi 409. Kerusakan data lain
        # (mis. FK) tidak boleh tersamar jadi 409; monkeypatch memalsukan galat FK itu.
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        def gagal_fk(db, row):
            raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

        monkeypatch.setattr(repository, "add_publication", gagal_fk)

        with pytest.raises(IntegrityError):
            publication.publish(db_session, report_id=report.id, actor_id=admin.id)

        assert db_session.get(Report, report.id).status == ReportStatus.SAVED
        assert _jumlah_terbit(db_session) == 0
