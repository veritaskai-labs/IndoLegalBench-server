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
from app.modules.reports.models import MeasurementKind, ReportCaseBasis
from app.modules.runs.models import (
    Attempt,
    AttemptEvidence,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    EvidenceFile,
    EvidenceKind,
)
from app.modules.runs.schemas import (
    AttemptGradeResult,
    CaseGradeResult,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
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


# --- Satu kasus sampai jawaban asli (AC2, AC3) -----------------------------------


def _url_kasus(report_id, result_id, case_id) -> str:
    return f"{_url(report_id, result_id)}/{case_id}"


def _case_id(client, report, hasil) -> str:
    return client.get(_url(report.id, hasil.id)).json()["cases"][0]["case_id"]


def test_admin_membuka_isi_kasus_dan_versi_rubrik(client, db_session, angka):
    """Positif: Admin membuka kasus di balik angka sampai isi dan versi rubrik (AC2)."""
    # Arrange
    report, hasil = angka
    complete_login(client, db_session, ADMIN_SUB)
    case_id = _case_id(client, report, hasil)

    # Act
    respons = client.get(_url_kasus(report.id, hasil.id, case_id))

    # Assert
    assert respons.status_code == 200
    body = respons.json()
    assert (body["case_code"], body["title"], body["rubric_version"], body["det_score"]) == (
        "UAT-PHK-001",
        "PHK sepihak",
        "v1",
        "0.80",
    )


def test_viewer_ditolak_403_dengan_email_kontak(client, db_session, angka):
    """Negatif: Viewer murni tidak melihat isi kasus; pesannya menunjuk email (AC3)."""
    # Arrange
    report, hasil = angka
    complete_login(client, db_session, VIEWER_SUB)
    case_id = _case_id(client, report, hasil)

    # Act
    respons = client.get(_url_kasus(report.id, hasil.id, case_id))

    # Assert
    assert respons.status_code == 403
    assert respons.json()["code"] == drilldown.CASE_CONTENT_FORBIDDEN
    assert "indolegalbench@veritask.ai" in respons.json()["message"]


# --- Bukti jawaban manual (AC4) ---------------------------------------------------


@pytest.fixture
def berkas_bukti(db_session, angka):
    """Satu berkas PDF bukti pada percobaan manual kasus dasar laporan."""
    report, hasil = angka
    basis = db_session.scalar(select(ReportCaseBasis).where(ReportCaseBasis.report_id == report.id))
    snapshot = CaseSnapshot(session_id=hasil.session_id)
    db_session.add(snapshot)
    db_session.flush()
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=basis.case_id,
        case_version_id=basis.case_version_id,
        body={},
    )
    db_session.add(item)
    db_session.flush()
    percobaan = Attempt(
        session_id=hasil.session_id,
        snapshot_item_id=item.id,
        repetition_no=1,
        source=AttemptSource.MANUAL,
    )
    db_session.add(percobaan)
    db_session.flush()
    db_session.add(EvidenceFile(key="bukti-326", content=b"%PDF-1.4 bukti"))
    bukti = AttemptEvidence(
        attempt_id=percobaan.id,
        kind=EvidenceKind.FILE,
        file_key="bukti-326",
        file_name="tangkapan layar.pdf",
        mime_type="application/pdf",
        size_bytes=14,
        uploaded_by=report.created_by,
    )
    db_session.add(bukti)
    db_session.commit()
    return report, hasil, basis.case_id, bukti.id


def test_admin_mengunduh_bukti_dari_penelusuran(client, db_session, berkas_bukti):
    """Positif: Admin membuka daftar bukti lalu mengunduh berkasnya sebagai lampiran (AC4)."""
    # Arrange
    report, hasil, case_id, bukti_id = berkas_bukti
    complete_login(client, db_session, ADMIN_SUB)
    kasus = _url_kasus(report.id, hasil.id, case_id)

    # Act
    daftar = client.get(f"{kasus}/attempts/1/evidence")
    unduh = client.get(f"{kasus}/evidence/{bukti_id}/file")

    # Assert
    assert [b["id"] for b in daftar.json()] == [str(bukti_id)]
    assert unduh.status_code == 200
    assert unduh.content == b"%PDF-1.4 bukti"
    assert unduh.headers["content-disposition"].startswith("attachment;")
    assert unduh.headers["x-content-type-options"] == "nosniff"


def test_viewer_tidak_bisa_membuka_bukti(client, db_session, berkas_bukti):
    """Negatif: bukti jawaban manual hanya Admin dan Kontributor (AC4), Viewer 403 di route."""
    # Arrange
    report, hasil, case_id, bukti_id = berkas_bukti
    complete_login(client, db_session, VIEWER_SUB)
    kasus = _url_kasus(report.id, hasil.id, case_id)

    # Act
    daftar = client.get(f"{kasus}/attempts/1/evidence")
    unduh = client.get(f"{kasus}/evidence/{bukti_id}/file")

    # Assert
    assert (daftar.status_code, unduh.status_code) == (403, 403)


# --- Jawaban produk lain pada kasus yang sama (PBI-16 SCRUM-370) -----------------


@pytest.fixture
def dua_produk(db_session, angka):
    """Laporan `angka` ditambah produk Kompetitor yang menjawab kasus yang sama (AC1 SCRUM-370)."""
    report, hasil = angka
    run_utama = db_session.scalar(select(GradingRun))
    admin = db_session.get(User, run_utama.triggered_by)
    sesi = pabrik.buat_sesi(db_session, admin, "Kompetitor")
    hasil_lain = pabrik.hasil(report, sesi, rank=2)
    db_session.add_all(
        [
            pabrik.sesi_laporan(
                report,
                sesi,
                product_label="Kompetitor",
                measurement_kind=MeasurementKind.AUTOMATIC,
            ),
            hasil_lain,
        ]
    )
    run = GradingRun(
        session_id=sesi.id,
        rubric_version_id=run_utama.rubric_version_id,
        kind=GradingRunKind.INITIAL,
        status=GradingRunStatus.FINISHED,
        is_latest=True,
        triggered_by=admin.id,
        triggered_at=DIBUAT - timedelta(hours=2),
        finished_at=DIBUAT - timedelta(hours=1),
    )
    db_session.add(run)
    db_session.commit()
    case_id = db_session.scalar(
        select(ReportCaseBasis.case_id).where(ReportCaseBasis.report_id == report.id)
    )
    kosong = ScorePair()
    drilldown.DbGradingStore(db_session).save_grading_run(
        SessionGradingResponse(
            grading_run_id=run.id,
            session_id=sesi.id,
            rubric_version="v2",
            kind="initial",
            status="completed",
            triggered_by=admin.id,
            triggered_at=run.triggered_at,
            scores=ThreeScores(dev=kosong, test=kosong, combined=kosong),
            cases=[
                CaseGradeResult(
                    snapshot_item_id=uuid.uuid4(),
                    case_id=case_id,
                    case_code="UAT-PHK-001",
                    title="PHK sepihak",
                    split_tag=SplitTag.TEST,
                    scores=ScorePair(deterministic=Decimal("0.55"), quality=Decimal("0.45")),
                    attempts=[
                        AttemptGradeResult(
                            attempt_id=uuid.uuid4(),
                            repetition_no=1,
                            status=AttemptStatus.SUCCEEDED,
                            is_technical_disruption=False,
                            score=ScorePair(deterministic=Decimal("0.55"), quality=Decimal("0.45")),
                            answer_text="Jawaban Kompetitor tentang PHK.",
                            reasons=["CITATION_OK"],
                        )
                    ],
                )
            ],
        )
    )
    return report, hasil, case_id, hasil_lain


def test_admin_melihat_jawaban_produk_lain_pada_kasus_yang_sama(client, db_session, dua_produk):
    """Positif: detail kasus membawa produk lain beserta jawaban asli, skor, dan rubriknya."""
    # Arrange
    report, hasil, case_id, hasil_lain = dua_produk
    complete_login(client, db_session, ADMIN_SUB)

    # Act
    respons = client.get(_url_kasus(report.id, hasil.id, case_id))

    # Assert
    assert respons.status_code == 200
    (lain,) = respons.json()["other_products"]
    assert (lain["result_id"], lain["product_label"], lain["status"], lain["rubric_version"]) == (
        str(hasil_lain.id),
        "Kompetitor",
        "answered",
        "v2",
    )
    assert (lain["det_score"], lain["llm_score"]) == ("0.55", "0.45")
    assert lain["attempts"][0]["answer_text"] == "Jawaban Kompetitor tentang PHK."


def test_perbandingan_tidak_membawa_id_sesi_produk_lain(client, db_session, dua_produk):
    """Negatif: id sesi produk lain tidak keluar, sama seperti produk utama (review #145)."""
    # Arrange
    report, hasil, case_id, _ = dua_produk
    complete_login(client, db_session, ADMIN_SUB)

    # Act
    respons = client.get(_url_kasus(report.id, hasil.id, case_id))

    # Assert
    (lain,) = respons.json()["other_products"]
    assert "session_id" not in lain


def test_viewer_tidak_menerima_jawaban_produk_lain(client, db_session, dua_produk):
    """Negatif: Viewer murni ditolak 403 di server dan tidak ada jawaban produk lain di badan."""
    # Arrange
    report, hasil, case_id, _ = dua_produk
    complete_login(client, db_session, VIEWER_SUB)

    # Act
    respons = client.get(_url_kasus(report.id, hasil.id, case_id))

    # Assert
    assert respons.status_code == 403
    assert "Jawaban Kompetitor" not in respons.text
    assert "other_products" not in respons.json()
