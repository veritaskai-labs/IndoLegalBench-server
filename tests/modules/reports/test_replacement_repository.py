"""Query kandidat ganti versi dengan himpunan produk sama. PBI-17 AC9, SCRUM-371.

SQLite lewat fixture db_session, data sintetis (L3). Jumlah statement diukur dengan
listener SQLAlchemy `before_cursor_execute` pada engine tes: kandidat dihitung dalam satu
query, jadi jumlahnya tetap sama untuk 1 atau 50 laporan (tanpa N+1).
"""

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.modules.reports import publication, repository
from app.modules.reports.models import ReportStatus, ScoreScope
from tests import replacement_world as dunia
from tests import reports_factory as pabrik


@pytest.fixture
def produk(db_session):
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = {nama: pabrik.buat_sesi(db_session, admin, f"Produk {nama}") for nama in "ABC"}
    return admin, rubrik, sesi


@pytest.fixture
def hitung_statement(db_session):
    """Jumlah statement SQL yang dikirim selama blok `with hitung_statement() as n:`."""
    engine = db_session.get_bind()

    class Penghitung:
        def __init__(self):
            self.jumlah = 0

        def _catat(self, *_args):
            self.jumlah += 1

        def __enter__(self):
            event.listen(engine, "before_cursor_execute", self._catat)
            return self

        def __exit__(self, *_exc):
            event.remove(engine, "before_cursor_execute", self._catat)

    return Penghitung


def _terbitkan(db, admin, report, supersedes_id=None):
    return publication.publish(
        db, report_id=report.id, actor_id=admin.id, supersedes_id=supersedes_id
    )


def _ids(rows):
    return [row.publication.report_id for row in rows]


class TestCurrentPublicationOf:
    def test_a_published_report_returns_its_publication(self, db_session, produk):
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        versi = _terbitkan(db_session, admin, report)

        assert repository.current_publication_of(db_session, report.id).id == versi.id

    def test_a_superseded_or_saved_report_has_no_current_publication(self, db_session, produk):
        admin, rubrik, sesi = produk
        lama = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        v1 = _terbitkan(db_session, admin, lama)
        baru = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        tersimpan = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, baru, v1.id)

        assert repository.current_publication_of(db_session, lama.id) is None
        assert repository.current_publication_of(db_session, tersimpan.id) is None


class TestCandidatesForASavedReport:
    """Jalur (a): laporan tersimpan dibuka, Admin memilih versi terbit yang diganti."""

    def test_only_current_publications_with_the_same_products_are_listed(self, db_session, produk):
        # Arrange
        admin, rubrik, sesi = produk
        ab = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]], title="AB")
        ba = dunia.simpan(db_session, admin, rubrik, [sesi["B"], sesi["A"]], title="BA")
        abc = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"], sesi["C"]])
        ac = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])
        a = dunia.simpan(db_session, admin, rubrik, [sesi["A"]])
        for report in (ab, ba, abc, ac, a):
            _terbitkan(db_session, admin, report)
        dibuka = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [dunia.sesi_baru(db_session, admin, sesi["B"]), sesi["A"]],
            labels=["Product B", "Product A"],
        )

        # Act
        kandidat = repository.replace_candidates_for_saved(db_session, dibuka.id)

        # Assert: urutan dan label tidak berpengaruh; superset, subset, beda produk tidak ikut
        assert sorted(_ids(kandidat)) == sorted([ab.id, ba.id])

    def test_newest_publication_comes_first(self, db_session, produk):
        admin, rubrik, sesi = produk
        laporan = [
            dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]], title=str(i))
            for i in range(3)
        ]
        for report in laporan:
            _terbitkan(db_session, admin, report)
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        kandidat = repository.replace_candidates_for_saved(db_session, dibuka.id)

        waktu = [row.publication.published_at for row in kandidat]
        assert waktu == sorted(waktu, reverse=True)
        assert len(kandidat) == 3

    def test_superseded_versions_are_not_listed(self, db_session, produk):
        admin, rubrik, sesi = produk
        lama = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        v1 = _terbitkan(db_session, admin, lama)
        baru = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, baru, v1.id)
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        kandidat = repository.replace_candidates_for_saved(db_session, dibuka.id)

        assert _ids(kandidat) == [baru.id]

    def test_no_publication_at_all_gives_an_empty_list(self, db_session, produk):
        admin, rubrik, sesi = produk
        dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        assert repository.replace_candidates_for_saved(db_session, dibuka.id) == []

    def test_a_report_never_lists_itself(self, db_session, produk):
        # Laporan terbit yang dibuka di jalur ini tidak pernah terjadi lewat service,
        # tetapi query-nya tetap tidak boleh mengembalikan laporan itu sendiri.
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, report)

        assert repository.replace_candidates_for_saved(db_session, report.id) == []


class TestCandidatesForAPublishedReport:
    """Jalur (b): laporan terbit dibuka, Admin memilih laporan tersimpan penggantinya."""

    def test_only_saved_test_reports_with_the_same_products_are_listed(self, db_session, produk):
        # Arrange
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, dibuka)
        lain_pemilik = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["B"], sesi["A"]],
            created_by=pabrik.buat_admin(db_session).id,
        )
        sama = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"], sesi["C"]])
        dunia.simpan(db_session, admin, rubrik, [sesi["A"]])
        dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["A"], sesi["B"]],
            score_scope=ScoreScope.DEV_TEST,
            has_dev_scores=True,
        )
        terbit_lain = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, terbit_lain)

        # Act
        kandidat = repository.replace_candidates_for_published(db_session, dibuka.id)

        # Assert: milik pengguna lain ikut (Admin boleh), dev dan yang sudah terbit tidak
        assert sorted(report.id for report in kandidat) == sorted([lain_pemilik.id, sama.id])
        assert {report.status for report in kandidat} == {ReportStatus.SAVED}

    def test_newest_created_report_comes_first(self, db_session, produk):
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, dibuka)
        awal = datetime(2026, 11, 1, tzinfo=UTC)
        dibuat = [
            dunia.simpan(
                db_session,
                admin,
                rubrik,
                [sesi["A"], sesi["B"]],
                created_at=awal + timedelta(days=hari),
            )
            for hari in (1, 3, 2)
        ]

        kandidat = repository.replace_candidates_for_published(db_session, dibuka.id)

        assert [report.id for report in kandidat] == [dibuat[1].id, dibuat[2].id, dibuat[0].id]

    def test_no_saved_report_gives_an_empty_list(self, db_session, produk):
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, dibuka)

        assert repository.replace_candidates_for_published(db_session, dibuka.id) == []

    def test_a_saved_report_never_lists_itself(self, db_session, produk):
        admin, rubrik, sesi = produk
        tersimpan = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        assert repository.replace_candidates_for_published(db_session, tersimpan.id) == []


class TestNoNPlusOne:
    @pytest.mark.parametrize(
        ("fungsi", "terbit"),
        [
            (repository.replace_candidates_for_saved, True),
            (repository.replace_candidates_for_published, False),
        ],
        ids=["saved-path", "published-path"],
    )
    def test_one_statement_for_1_or_50_matching_reports(
        self, db_session, produk, hitung_statement, fungsi, terbit
    ):
        # Arrange: 1 laporan cocok, ukur; lalu 49 lagi, ukur ulang
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        if not terbit:
            _terbitkan(db_session, admin, dibuka)

        def tambah(n):
            for _ in range(n):
                report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
                if terbit:
                    _terbitkan(db_session, admin, report)

        tambah(1)
        with hitung_statement() as satu:
            hasil_satu = fungsi(db_session, dibuka.id)
        tambah(49)
        mulai = time.perf_counter()
        with hitung_statement() as lima_puluh:
            hasil_lima_puluh = fungsi(db_session, dibuka.id)
        durasi_ms = (time.perf_counter() - mulai) * 1000

        # Assert
        assert (len(hasil_satu), len(hasil_lima_puluh)) == (1, 50)
        assert satu.jumlah == lima_puluh.jumlah == 1
        print(f"{fungsi.__name__}: 50 kandidat, 1 statement, {durasi_ms:.1f} ms")
