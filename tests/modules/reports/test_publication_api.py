"""HTTP penerbitan dan daftar laporan terbit. PBI-17 SCRUM-84 SCRUM-332.

Lewat aplikasi utuh: login, router, service, dan SQLite. Aturan bisnisnya diuji di
test_publication.py. Di sini yang dipastikan sambungan HTTP-nya.
"""

import uuid

import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.auth.seeds import (
    ADMIN_SUB,
    AUTHOR_SUB,
    DEVELOPER_AI_SUB,
    VIEWER_SUB,
    seed_users,
)
from app.modules.reports import repository
from app.modules.reports.models import ProductLabelMode, ReportStatus, ScoreScope
from tests import reports_factory as pabrik
from tests.login import complete_login


def _id(db, email: str):
    return db.scalar(select(User.id).where(User.email == email))


def _simpan(db, pembuat, admin, rubrik, sesi, **ubah):
    label = ubah.pop("product_label", "Gemini")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1, created_by=pembuat, **ubah)
    repository.add_report(
        db,
        report,
        [pabrik.sesi_laporan(report, sesi, product_label=label)],
        [pabrik.hasil(report, sesi)],
        [pabrik.dasar(report)],
    )
    db.commit()
    return report


@pytest.fixture
def dunia(db_session):
    seed_users(db_session)
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = pabrik.buat_sesi(db_session, admin, "Gemini")
    kontributor = _id(db_session, "author@veritask.test")
    return {
        "admin": admin,
        "rubrik": rubrik,
        "sesi": sesi,
        "kontributor": kontributor,
        "tersimpan": _simpan(db_session, kontributor, admin, rubrik, sesi),
    }


def test_publish_and_the_list_require_login(client):
    """Negatif: tidak ada tautan publik (AC9)."""
    # Act
    terbit = client.post(f"/reports/{uuid.uuid4()}/publish")
    daftar = client.get("/published-reports")

    # Assert
    assert terbit.status_code == 401
    assert daftar.status_code == 401


@pytest.mark.parametrize("sub", [VIEWER_SUB, AUTHOR_SUB, DEVELOPER_AI_SUB])
def test_only_an_admin_can_publish(client, db_session, dunia, sub):
    """Negatif: peran lain 403, dan laporannya tetap tersimpan."""
    # Arrange
    complete_login(client, db_session, sub=sub)
    report = dunia["tersimpan"]

    # Act
    respons = client.post(f"/reports/{report.id}/publish", json={})

    # Assert
    assert respons.status_code == 403
    assert respons.json()["code"] == "FORBIDDEN"
    assert db_session.get(report.__class__, report.id).status == ReportStatus.SAVED


def test_admin_publishes_someone_elses_saved_report(client, db_session, dunia):
    """Positif: Admin menerbitkan laporan tersimpan siapa pun (D9)."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    report = dunia["tersimpan"]

    # Act
    respons = client.post(f"/reports/{report.id}/publish")

    # Assert
    assert respons.status_code == 201
    badan = respons.json()
    assert (badan["report_id"], badan["version_no"], badan["supersedes_id"]) == (
        str(report.id),
        1,
        None,
    )
    assert db_session.get(report.__class__, report.id).status == ReportStatus.PUBLISHED


def test_the_client_cannot_choose_the_version_number(client, db_session, dunia):
    """Negatif: version_no diisi server. Field lain pada badan juga ditolak."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    report = dunia["tersimpan"]

    # Act
    respons = client.post(f"/reports/{report.id}/publish", json={"version_no": 5})

    # Assert
    assert respons.status_code == 422
    assert db_session.get(report.__class__, report.id).status == ReportStatus.SAVED


def test_dev_scores_are_rejected_with_409(client, db_session, dunia):
    """Negatif: laporan skor dev tidak terbit (AC12, D1 aturan 1)."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    report = _simpan(
        db_session,
        dunia["kontributor"],
        dunia["admin"],
        dunia["rubrik"],
        dunia["sesi"],
        score_scope=ScoreScope.DEV,
        has_dev_scores=True,
        title="Laporan dev",
    )

    # Act
    respons = client.post(f"/reports/{report.id}/publish", json={})

    # Assert
    assert respons.status_code == 409
    assert respons.json()["code"] == "REPORT_HAS_DEV_SCORES"
    assert db_session.get(report.__class__, report.id).status == ReportStatus.SAVED


def test_an_unknown_report_is_404_and_republish_is_409(client, db_session, dunia):
    """Negatif: laporan yang tidak ada, dan terbit dua kali, bukan kode skor dev."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    report = dunia["tersimpan"]
    client.post(f"/reports/{report.id}/publish", json={})

    # Act
    tidak_ada = client.post(f"/reports/{uuid.uuid4()}/publish", json={})
    kedua = client.post(f"/reports/{report.id}/publish", json={})

    # Assert
    assert tidak_ada.status_code == 404
    assert tidak_ada.json()["code"] == "REPORT_NOT_FOUND"
    assert kedua.status_code == 409
    assert kedua.json()["code"] == "REPORT_NOT_PUBLISHABLE"


def test_replace_keeps_the_old_report_readable_and_off_the_list(client, db_session, dunia):
    """Positif: versi baru mengganti, versi lama tetap tersimpan dan masih bisa dibuka."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    lama = dunia["tersimpan"]
    baru = _simpan(
        db_session,
        dunia["kontributor"],
        dunia["admin"],
        dunia["rubrik"],
        dunia["sesi"],
        title="Laporan perbaikan",
    )
    versi_lama = client.post(f"/reports/{lama.id}/publish", json={}).json()

    # Act
    versi_baru = client.post(
        f"/reports/{baru.id}/publish",
        json={"supersedes_id": versi_lama["id"]},
    )
    daftar = client.get("/published-reports")
    client.cookies.clear()
    complete_login(client, db_session, sub=VIEWER_SUB)
    lama_dibuka = client.get(f"/reports/{lama.id}")
    daftar_viewer = client.get("/published-reports")

    # Assert
    assert versi_baru.status_code == 201
    assert versi_baru.json()["version_no"] == 2
    assert versi_baru.json()["supersedes_id"] == versi_lama["id"]
    assert [item["report_id"] for item in daftar.json()["items"]] == [str(baru.id)]
    assert lama_dibuka.status_code == 200
    assert lama_dibuka.json()["status"] == "superseded"
    assert [item["title"] for item in daftar_viewer.json()["items"]] == ["Laporan perbaikan"]


def test_the_published_list_uses_frozen_labels(client, db_session, dunia):
    """AC10: nama di daftar adalah label beku, bukan nama produk yang hidup."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    report = _simpan(
        db_session,
        dunia["kontributor"],
        dunia["admin"],
        dunia["rubrik"],
        dunia["sesi"],
        product_label="Product A",
        product_label_mode=ProductLabelMode.CODE,
        title="Laporan kode",
    )

    # Act
    client.post(f"/reports/{report.id}/publish", json={})
    daftar = client.get("/published-reports")

    # Assert
    item = daftar.json()["items"][0]
    assert item["product_labels"] == ["Product A"]
    assert "product_id" not in item
    assert "session_id" not in item
    assert "Gemini" not in daftar.text


def test_a_logged_in_developer_can_read_the_list_but_not_publish(client, db_session, dunia):
    """D9: daftar terbit untuk semua yang login. Menerbitkan tetap Admin."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)
    client.post(f"/reports/{dunia['tersimpan'].id}/publish", json={})
    client.cookies.clear()
    complete_login(client, db_session, sub=DEVELOPER_AI_SUB)

    # Act
    daftar = client.get("/published-reports")
    terbit = client.post(f"/reports/{uuid.uuid4()}/publish", json={})

    # Assert
    assert daftar.status_code == 200
    assert daftar.json()["total"] == 1
    assert terbit.status_code == 403
