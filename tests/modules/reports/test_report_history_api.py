"""Test HTTP riwayat laporan dan unduh PDF (PBI-15 SCRUM-320, diagram D3 langkah 10-15).

Lewat aplikasi utuh: login fake OIDC per peran (tests/login.py), router, service,
repository, dan SQLite. Yang dipastikan di sini sambungan HTTP-nya: status kode, bentuk
body, header unduhan, dan bahwa aturan D9 juga berlaku di endpoint. Rincian aturan D9
dan perakitan hasil beku diuji di test_report_access.py dan test_report_history_service.py.

Label: Positif = boleh dan berhasil, Negatif = ditolak, Edge = masukan tidak valid.
"""

import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB, VIEWER_SUB, seed_users
from app.modules.cases.models import SplitTag
from app.modules.reports import repository
from app.modules.reports.models import ReportStatus, ScoreScope, ScoreSplit
from tests import reports_factory as pabrik
from tests.login import complete_login


def _id_pengguna(db, email):
    return db.scalar(select(User.id).where(User.email == email))


def _simpan(db, admin, rubrik, sesi, created_by, **ubah):
    """Satu laporan lengkap berisi satu sesi, dibuat oleh pengguna tertentu."""
    report = pabrik.laporan(admin, rubrik, case_basis_count=1, created_by=created_by, **ubah)
    dev = report.score_scope is not ScoreScope.TEST
    repository.add_report(
        db,
        report,
        [pabrik.sesi_laporan(report, sesi, product_label="Gemini")],
        [pabrik.hasil(report, sesi, score_split=ScoreSplit.DEV if dev else ScoreSplit.TEST)],
        [pabrik.dasar(report, split_tag=SplitTag.DEV if dev else SplitTag.TEST)],
    )
    db.commit()
    return report


@pytest.fixture
def laporan(db_session):
    """Laporan milik Viewer, laporan tersimpan Kontributor, satu laporan terbit, dan satu dev."""
    seed_users(db_session)
    viewer = _id_pengguna(db_session, "viewer@veritask.test")
    kontributor = _id_pengguna(db_session, "author@veritask.test")
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = pabrik.buat_sesi(db_session, admin, "Gemini")
    return {
        "milik_viewer": _simpan(db_session, admin, rubrik, sesi, viewer),
        "milik_kontributor": _simpan(db_session, admin, rubrik, sesi, kontributor),
        "terbit": _simpan(
            db_session, admin, rubrik, sesi, kontributor, status=ReportStatus.PUBLISHED
        ),
        "dev": _simpan(
            db_session,
            admin,
            rubrik,
            sesi,
            admin.id,
            score_scope=ScoreScope.DEV,
            has_dev_scores=True,
        ),
    }


def test_tanpa_login_ditolak_401(client):
    """Negatif: laporan hanya untuk pengguna login, tidak ada tautan publik (D9)."""
    # Act
    respons = client.get("/reports")

    # Assert
    assert respons.status_code == 401


def test_viewer_melihat_laporan_miliknya_dan_yang_terbit(client, db_session, laporan):
    """Positif dan negatif: laporan tersimpan orang lain dan laporan dev tidak ikut di daftar."""
    # Arrange
    complete_login(client, db_session, sub=VIEWER_SUB)

    # Act
    respons = client.get("/reports", params={"page": 1, "size": 10})

    # Assert
    assert respons.status_code == 200
    body = respons.json()
    assert {item["id"] for item in body["items"]} == {
        str(laporan["milik_viewer"].id),
        str(laporan["terbit"].id),
    }
    assert (body["total"], body["page"], body["size"]) == (2, 1, 10)


def test_detail_mengembalikan_hasil_beku(client, db_session, laporan):
    """Positif: laporan dibuka ulang dengan angka, label, dan versi yang tersimpan (AC riwayat)."""
    # Arrange
    complete_login(client, db_session, sub=VIEWER_SUB)
    report = laporan["milik_viewer"]

    # Act
    respons = client.get(f"/reports/{report.id}")

    # Assert
    assert respons.status_code == 200
    body = respons.json()
    assert (body["title"], body["rubric_version_no"], body["status"]) == (
        "Laporan Oktober",
        1,
        "saved",
    )
    sesi = body["sessions"][0]
    assert (sesi["product_label"], sesi["measurement_kind"], sesi["is_trial"]) == (
        "Gemini",
        "manual",
        False,
    )
    hasil = sesi["results"][0]
    assert (hasil["det_score"], hasil["llm_score"], hasil["rank"]) == ("0.75", "0.50", 1)


@pytest.mark.parametrize(
    ("sub", "nama"),
    [
        # Negatif: laporan tersimpan Kontributor dibuka Viewer (D9, hanya pembuat dan Admin).
        (VIEWER_SUB, "milik_kontributor"),
        # Negatif: laporan dev dibuka Kontributor (skor dev tidak pernah ke FE untuk peran ini).
        (AUTHOR_SUB, "dev"),
    ],
)
def test_detail_yang_tidak_boleh_dibaca_dijawab_404(client, db_session, laporan, sub, nama):
    """Negatif: dijawab sama dengan laporan yang tidak ada, jadi keberadaannya tidak bocor."""
    # Arrange
    complete_login(client, db_session, sub=sub)

    # Act
    respons = client.get(f"/reports/{laporan[nama].id}")

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "REPORT_NOT_FOUND"


def test_admin_membuka_laporan_tersimpan_orang_lain(client, db_session, laporan):
    """Positif: Admin membuka laporan tersimpan siapa pun, misalnya sebelum menerbitkannya."""
    # Arrange
    complete_login(client, db_session, sub=ADMIN_SUB)

    # Act
    respons = client.get(f"/reports/{laporan['milik_kontributor'].id}")

    # Assert
    assert respons.status_code == 200


def test_id_bukan_uuid_ditolak_422(client, db_session, laporan):
    """Edge: path yang bukan UUID ditolak validasi sebelum menyentuh database."""
    # Arrange
    complete_login(client, db_session, sub=VIEWER_SUB)

    # Act
    respons = client.get("/reports/bukan-uuid")

    # Assert
    assert respons.status_code == 422


def test_unduh_pdf_sebagai_lampiran(client, db_session, laporan):
    """Positif: PDF siap presentasi diunduh dari laporan tersimpan (AC tampilan web dan PDF)."""
    # Arrange
    complete_login(client, db_session, sub=VIEWER_SUB)
    report = laporan["milik_viewer"]

    # Act
    respons = client.get(f"/reports/{report.id}/pdf")

    # Assert
    assert respons.status_code == 200
    assert respons.headers["content-type"] == "application/pdf"
    assert respons.headers["content-disposition"] == (
        f'attachment; filename="laporan-{report.id}.pdf"'
    )
    assert respons.content.startswith(b"%PDF")


def test_unduh_pdf_laporan_yang_tidak_boleh_dibaca_dijawab_404(client, db_session, laporan):
    """Negatif: unduhan memakai aturan baca yang sama dengan halaman laporan."""
    # Arrange
    complete_login(client, db_session, sub=VIEWER_SUB)

    # Act
    respons = client.get(f"/reports/{laporan['dev'].id}/pdf")

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "REPORT_NOT_FOUND"
