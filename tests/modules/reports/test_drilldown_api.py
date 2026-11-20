"""Test HTTP penelusuran angka laporan (PBI-16 SCRUM-326, diagram D6 langkah 1-3).

Lewat aplikasi utuh: login fake OIDC per peran (tests/login.py), router, drilldown,
repository, dan SQLite, termasuk pencarian run beku dari tabel grading_runs. Hanya
perakit run penilaian (DbGradingStore) yang diganti MemoryGradingStore, karena perakit
itu sudah diuji di modul runs. Rincian aturan per peran ada di test_drilldown_service.py.

Label: Positif = boleh dan berhasil, Negatif = ditolak, Edge = masukan tidak valid.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB, VIEWER_SUB, seed_users
from app.modules.cases.models import CaseStatus, CaseVersion, SplitTag
from app.modules.grading.models import GradingRun, GradingRunKind, GradingRunStatus
from app.modules.reports import drilldown, repository
from app.modules.runs.schemas import CaseGradeResult, ScorePair, SessionGradingResponse, ThreeScores
from app.modules.runs.session_grade import MemoryGradingStore
from tests import reports_factory as pabrik
from tests.login import complete_login

DIBUAT = datetime(2026, 12, 1, 10, tzinfo=UTC)


@pytest.fixture
def angka(db_session, monkeypatch):
    """Satu laporan test milik Viewer dengan satu kasus dasar dan run beku yang sudah dinilai."""
    seed_users(db_session)
    viewer = db_session.scalar(select(User.id).where(User.email == "viewer@veritask.test"))
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = pabrik.buat_sesi(db_session, admin, "Justisio")
    versi = CaseVersion(
        case_id=uuid.uuid4(),
        version_no=2,
        status=CaseStatus.APPROVED,
        case_code="UAT-PHK-001",
        split_tag=SplitTag.TEST,
        content={},
        created_by=admin.id,
    )
    db_session.add(versi)
    db_session.flush()
    report = pabrik.laporan(admin, rubrik, case_basis_count=1, created_by=viewer, created_at=DIBUAT)
    hasil = pabrik.hasil(report, sesi)
    repository.add_report(
        db_session,
        report,
        [pabrik.sesi_laporan(report, sesi, product_label="Justisio")],
        [hasil],
        [pabrik.dasar(report, case_id=versi.case_id, case_version_id=versi.id)],
    )
    run = GradingRun(
        session_id=sesi.id,
        rubric_version_id=rubrik.id,
        kind=GradingRunKind.INITIAL,
        status=GradingRunStatus.FINISHED,
        is_latest=True,
        triggered_by=admin.id,
        triggered_at=DIBUAT - timedelta(hours=2),
        finished_at=DIBUAT - timedelta(hours=1),
    )
    db_session.add(run)
    db_session.commit()

    kosong = ScorePair()
    store = MemoryGradingStore()
    store.save_grading_run(
        SessionGradingResponse(
            grading_run_id=run.id,
            session_id=sesi.id,
            rubric_version="v1",
            kind="initial",
            status="completed",
            triggered_by=admin.id,
            triggered_at=run.triggered_at,
            scores=ThreeScores(dev=kosong, test=kosong, combined=kosong),
            cases=[
                CaseGradeResult(
                    snapshot_item_id=uuid.uuid4(),
                    case_id=versi.case_id,
                    case_code="UAT-PHK-001",
                    title="PHK sepihak",
                    split_tag=SplitTag.TEST,
                    scores=ScorePair(deterministic=Decimal("0.80"), quality=Decimal("0.75")),
                )
            ],
        )
    )
    monkeypatch.setattr(drilldown, "DbGradingStore", lambda db: store)
    return report, hasil


def _url(report_id, result_id) -> str:
    return f"/reports/{report_id}/results/{result_id}/cases"


def test_tanpa_login_ditolak_401(client):
    """Negatif: penelusuran hanya untuk pengguna login, tidak ada tautan publik (D9)."""
    # Act
    respons = client.get(_url(uuid.uuid4(), uuid.uuid4()))

    # Assert
    assert respons.status_code == 401


def test_viewer_membuka_angka_laporannya_menjadi_daftar_kasus(client, db_session, angka):
    """Positif: Viewer melihat kode, versi, dan skor kasus, tetapi isi kasus tertutup (AC1, AC3)."""
    # Arrange
    report, hasil = angka
    complete_login(client, db_session, VIEWER_SUB)

    # Act
    respons = client.get(_url(report.id, hasil.id))

    # Assert
    assert respons.status_code == 200
    body = respons.json()
    assert (body["product_label"], body["rubric_version"]) == ("Justisio", "v1")
    assert body["cases"] == [
        {
            "case_id": body["cases"][0]["case_id"],
            "case_code": "UAT-PHK-001",
            "case_version_no": 2,
            "split_tag": "test",
            "det_score": "0.80",
            "llm_score": "0.75",
            "content_visible": False,
        }
    ]


def test_angka_dari_laporan_lain_dijawab_404(client, db_session, angka):
    """Negatif: menukar id angka di URL tidak membuka angka di luar laporan ini."""
    # Arrange
    report, _ = angka
    complete_login(client, db_session, ADMIN_SUB)

    # Act
    respons = client.get(_url(report.id, uuid.uuid4()))

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == drilldown.REPORT_RESULT_NOT_FOUND


def test_laporan_tersimpan_orang_lain_dijawab_404(client, db_session, angka):
    """Negatif: Kontributor tidak bisa menelusuri laporan tersimpan milik Viewer (D9)."""
    # Arrange
    report, hasil = angka
    complete_login(client, db_session, AUTHOR_SUB)

    # Act
    respons = client.get(_url(report.id, hasil.id))

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "REPORT_NOT_FOUND"


def test_id_bukan_uuid_ditolak_422(client, db_session, angka):
    """Edge: id angka yang bukan UUID ditolak sebelum menyentuh database."""
    # Arrange
    report, _ = angka
    complete_login(client, db_session, ADMIN_SUB)

    # Act
    respons = client.get(_url(report.id, "bukan-uuid"))

    # Assert
    assert respons.status_code == 422
