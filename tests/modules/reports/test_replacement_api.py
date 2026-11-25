"""GET /reports/{id}/replace-candidates dan penolakan lewat HTTP. PBI-17 AC9, SCRUM-371.

Lewat aplikasi utuh (login, router, service, SQLite), data sintetis. Aturan himpunan produk
dan query-nya diuji di test_replacement.py dan test_replacement_repository.py; di sini
kontrak untuk SCRUM-373 (FE), hak akses (OWASP A01), dan jalur POST langsung tanpa FE.
"""

import uuid

import pytest
from sqlalchemy import event, func, select

from app.modules.auth.seeds import (
    ADMIN_SUB,
    AUTHOR_SUB,
    DEVELOPER_AI_SUB,
    VIEWER_SUB,
    seed_users,
)
from app.modules.reports import publication
from app.modules.reports.models import PublishedReport, ReportStatus, ScoreScope
from tests import replacement_world as dunia
from tests import reports_factory as pabrik
from tests.login import complete_login


@pytest.fixture
def produk(db_session):
    seed_users(db_session)
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = {nama: pabrik.buat_sesi(db_session, admin, f"Produk {nama}") for nama in "ABC"}
    return admin, rubrik, sesi


def _terbitkan(db, admin, report, supersedes_id=None):
    return publication.publish(
        db, report_id=report.id, actor_id=admin.id, supersedes_id=supersedes_id
    )


def _kandidat(client, report_id):
    return client.get(f"/reports/{report_id}/replace-candidates")


class TestAccess:
    def test_login_is_required(self, client, db_session, produk):
        admin, rubrik, sesi = produk
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])

        assert _kandidat(client, report.id).status_code == 401

    @pytest.mark.parametrize("sub", [AUTHOR_SUB, VIEWER_SUB, DEVELOPER_AI_SUB])
    def test_only_an_admin_sees_the_candidates(self, client, db_session, produk, sub):
        # A01: peran yang tidak boleh menerbitkan tidak menerima daftar laporan lain
        admin, rubrik, sesi = produk
        terbit = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, terbit)
        report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        complete_login(client, db_session, sub=sub)

        respons = _kandidat(client, report.id)

        assert respons.status_code == 403
        assert "items" not in respons.json()


class TestSavedReportPath:
    def test_lists_current_versions_with_the_same_products(self, client, db_session, produk):
        # Arrange
        admin, rubrik, sesi = produk
        ab = dunia.simpan(
            db_session, admin, rubrik, [sesi["A"], sesi["B"]], labels=["Alfa", "Beta"]
        )
        v1 = _terbitkan(db_session, admin, ab)
        ac = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])
        _terbitkan(db_session, admin, ac)
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["B"], sesi["A"]])
        complete_login(client, db_session, sub=ADMIN_SUB)

        # Act
        respons = _kandidat(client, dibuka.id)

        # Assert: bentuk kontrak untuk SCRUM-373
        assert respons.status_code == 200
        badan = respons.json()
        assert (badan["mode"], badan["replaces_publication_id"]) == ("replace_published", None)
        assert len(badan["items"]) == 1
        item = badan["items"][0]
        assert item["report_id"] == str(ab.id)
        assert item["publication_id"] == str(v1.id)
        assert item["version_no"] == 1
        assert item["title"] == ab.title
        assert item["product_labels"] == ["Alfa", "Beta"]
        assert item["date"] is not None

    def test_an_empty_list_when_nothing_with_the_same_products_is_published(
        self, client, db_session, produk
    ):
        admin, rubrik, sesi = produk
        ac = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])
        _terbitkan(db_session, admin, ac)
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        complete_login(client, db_session, sub=ADMIN_SUB)

        badan = _kandidat(client, dibuka.id).json()

        assert (badan["mode"], badan["items"]) == ("replace_published", [])


class TestPublishedReportPath:
    def test_lists_saved_reports_and_the_version_to_replace(self, client, db_session, produk):
        # Arrange
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        v1 = _terbitkan(db_session, admin, dibuka)
        pengganti = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["B"], sesi["A"]],
            labels=["Beta", "Alfa"],
            title="Perbaikan",
        )
        dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"], sesi["C"]])
        complete_login(client, db_session, sub=ADMIN_SUB)

        # Act
        badan = _kandidat(client, dibuka.id).json()

        # Assert
        assert (badan["mode"], badan["replaces_publication_id"]) == (
            "replace_with_saved",
            str(v1.id),
        )
        assert [
            (item["report_id"], item["publication_id"], item["version_no"], item["title"])
            for item in badan["items"]
        ] == [(str(pengganti.id), None, None, "Perbaikan")]
        assert badan["items"][0]["product_labels"] == ["Alfa", "Beta"]

    def test_the_chosen_candidate_publishes_as_the_next_version(self, client, db_session, produk):
        # Positif ujung ke ujung: pilihan dari daftar langsung bisa dikirim ke POST publish
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, dibuka)
        dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        complete_login(client, db_session, sub=ADMIN_SUB)
        badan = _kandidat(client, dibuka.id).json()

        respons = client.post(
            f"/reports/{badan['items'][0]['report_id']}/publish",
            json={"supersedes_id": badan["replaces_publication_id"]},
        )

        assert respons.status_code == 201
        assert respons.json()["version_no"] == 2
        assert db_session.get(type(dibuka), dibuka.id).status == ReportStatus.SUPERSEDED

    def test_a_published_report_without_its_publication_row_is_refused(
        self, client, db_session, produk
    ):
        # Edge: data rusak (PG menolaknya lewat trigger, SQLite tidak); tidak boleh 500.
        admin, rubrik, sesi = produk
        rusak = dunia.simpan(
            db_session, admin, rubrik, [sesi["A"], sesi["B"]], status=ReportStatus.PUBLISHED
        )
        complete_login(client, db_session, sub=ADMIN_SUB)

        respons = _kandidat(client, rusak.id)

        assert respons.status_code == 409
        assert respons.json()["code"] == "REPORT_NOT_PUBLISHABLE"


class TestRefusals:
    def test_an_unknown_report_is_404(self, client, db_session, produk):
        complete_login(client, db_session, sub=ADMIN_SUB)

        respons = _kandidat(client, uuid.uuid4())

        assert respons.status_code == 404
        assert respons.json()["code"] == "REPORT_NOT_FOUND"

    def test_a_superseded_report_has_no_candidates(self, client, db_session, produk):
        admin, rubrik, sesi = produk
        lama = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        v1 = _terbitkan(db_session, admin, lama)
        baru = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        _terbitkan(db_session, admin, baru, v1.id)
        complete_login(client, db_session, sub=ADMIN_SUB)

        respons = _kandidat(client, lama.id)

        assert respons.status_code == 409
        assert respons.json()["code"] == "REPORT_NOT_PUBLISHABLE"

    def test_a_dev_report_has_no_candidates(self, client, db_session, produk):
        admin, rubrik, sesi = produk
        dev = dunia.simpan(
            db_session,
            admin,
            rubrik,
            [sesi["A"], sesi["B"]],
            score_scope=ScoreScope.DEV,
            has_dev_scores=True,
        )
        complete_login(client, db_session, sub=ADMIN_SUB)

        respons = _kandidat(client, dev.id)

        assert respons.status_code == 409
        assert respons.json()["code"] == "REPORT_HAS_DEV_SCORES"

    def test_a_direct_post_with_other_products_is_refused_with_a_clear_reason(
        self, client, db_session, produk
    ):
        # A04: aturan dijaga di server walau klien melewati dropdown
        admin, rubrik, sesi = produk
        lama = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        v1 = _terbitkan(db_session, admin, lama)
        lain = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["C"]])
        complete_login(client, db_session, sub=ADMIN_SUB)

        respons = client.post(f"/reports/{lain.id}/publish", json={"supersedes_id": str(v1.id)})

        assert respons.status_code == 409
        assert respons.json()["code"] == "REPORT_PRODUCTS_MISMATCH"
        assert "produk yang sama" in respons.json()["message"]
        assert db_session.scalar(select(func.count()).select_from(PublishedReport)) == 1


class TestServiceStatementCount:
    @pytest.mark.parametrize("terbit_dulu", [False, True], ids=["saved-path", "published-path"])
    def test_the_same_number_of_statements_for_1_or_50_candidates(
        self, db_session, produk, terbit_dulu
    ):
        # Arrange: hitung statement service utuh (laporan, versi, kandidat, label)
        admin, rubrik, sesi = produk
        dibuka = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
        if terbit_dulu:
            _terbitkan(db_session, admin, dibuka)
        dibuka_id = dibuka.id
        engine = db_session.get_bind()

        def tambah(n):
            for _ in range(n):
                report = dunia.simpan(db_session, admin, rubrik, [sesi["A"], sesi["B"]])
                if not terbit_dulu:
                    _terbitkan(db_session, admin, report)

        def ukur():
            jumlah = []
            catat = lambda *_args: jumlah.append(1)  # noqa: E731
            db_session.expire_all()
            event.listen(engine, "before_cursor_execute", catat)
            try:
                hasil = publication.replace_candidates(db_session, dibuka_id)
            finally:
                event.remove(engine, "before_cursor_execute", catat)
            return len(hasil.items), len(jumlah)

        # Act
        tambah(1)
        satu = ukur()
        tambah(49)
        lima_puluh = ukur()

        # Assert: laporan + kandidat + label (+ versi terbit di jalur published)
        harapan = 4 if terbit_dulu else 3
        assert satu == (1, harapan)
        assert lima_puluh == (50, harapan)
