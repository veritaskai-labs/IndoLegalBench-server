"""Unit test service riwayat laporan (PBI-15 SCRUM-320, diagram D3 langkah 10 dan 11).

Repository di-mock (create_autospec), jadi yang diuji hanya logika service: filter
D9 yang dikirim ke repository, penolakan laporan yang tidak boleh dibaca, dan cara hasil
beku dirakit menjadi ReportDetail tanpa menghitung ulang (keputusan SA di D1).

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas atau tidak rapi.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports import repository, service
from app.modules.reports.access import ReportVisibility
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    Report,
    ReportResult,
    ReportSession,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.repository import CaseBasisRow, ReportContents
from app.shared.exceptions import NotFoundError
from app.shared.pagination import PageParams
from app.shared.security import CurrentUser, Role

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
SESI_A = uuid.UUID("00000000-0000-0000-0000-00000000005a")
SESI_B = uuid.UUID("00000000-0000-0000-0000-00000000005b")
DB = object()  # Sesi database palsu: service hanya meneruskannya ke repository.


@pytest.fixture
def repo(monkeypatch):
    """Repository tiruan dengan tanda tangan fungsi asli, jadi salah argumen langsung gagal."""
    tiruan = create_autospec(repository, instance=True)
    monkeypatch.setattr(service, "repository", tiruan)
    return tiruan


def _pengguna(role=Role.VIEWER, user_id=PEMBUAT) -> CurrentUser:
    return CurrentUser(user_id=user_id, name="Uji", email="uji@veritask.test", role=role)


def _laporan(**ubah) -> Report:
    """Baris ORM yang belum disimpan; status dan created_at diisi karena default DB tidak jalan."""
    isi = {
        "id": uuid.uuid4(),
        "title": "Laporan Desember",
        "score_scope": ScoreScope.TEST,
        "has_dev_scores": False,
        "measurement_filter": MeasurementFilter.ALL,
        "product_label_mode": ProductLabelMode.REAL_NAME,
        "language": "id",
        "status": ReportStatus.SAVED,
        "rubric_version_id": uuid.uuid4(),
        "judge_model_name": "judge-2026-12",
        "case_basis_count": 2,
        "created_by": PEMBUAT,
        "created_at": datetime(2026, 12, 1, tzinfo=UTC),
    } | ubah
    return Report(**isi)


def _sesi(report: Report, session_id: uuid.UUID, label: str, **ubah) -> ReportSession:
    isi = {
        "report_id": report.id,
        "session_id": session_id,
        "product_id": uuid.uuid4(),
        "product_label": label,
        "measurement_kind": MeasurementKind.AUTOMATIC,
        "is_trial": False,
        "markers": [],
    } | ubah
    return ReportSession(**isi)


def _hasil(report: Report, session_id: uuid.UUID, rank: int, **ubah) -> ReportResult:
    isi = {
        "report_id": report.id,
        "session_id": session_id,
        "score_split": ScoreSplit.TEST,
        "cases_graded_count": 3,
        "cases_basis_count": 2,
        "det_score": Decimal("0.75"),
        "llm_score": Decimal("0.50"),
        "total_score": Decimal("0.63"),
        "failure_rate": Decimal("1.50"),
        "rank": rank,
        "repetitions": 3,
        "product_model_version": "model-2026-12",
        "judge_model_version": "judge-2026-12",
    } | ubah
    return ReportResult(**isi)


def _isi(report: Report, **ubah) -> ReportContents:
    isi = {
        "rubric_version_no": 4,
        "sessions": [_sesi(report, SESI_A, "Gemini"), _sesi(report, SESI_B, "Justisio")],
        "results": [_hasil(report, SESI_A, 1), _hasil(report, SESI_B, 2)],
        "case_basis": [CaseBasisRow("UAT-PHK-001", 2, SplitTag.TEST)],
    } | ubah
    return ReportContents(**isi)


# --- Daftar laporan tersimpan (GET /reports) ----------------------------------


def test_list_reports_mengirim_filter_d9_dan_halaman_ke_repository(repo):
    """Positif: Viewer hanya meminta laporan miliknya atau yang terbit, tanpa skor dev."""
    # Arrange
    laporan = _laporan()
    repo.list_reports.return_value = ([laporan], 41)

    # Act
    halaman = service.list_reports(DB, _pengguna(), PageParams(page=3, size=20))

    # Assert
    repo.list_reports.assert_called_once_with(
        DB,
        ReportVisibility(viewer_id=PEMBUAT, all_saved=False, include_dev=False),
        offset=40,
        limit=20,
    )
    assert (halaman.total, halaman.page, halaman.size) == (41, 3, 20)
    assert [item.id for item in halaman.items] == [laporan.id]
    assert halaman.items[0].title == "Laporan Desember"


def test_list_reports_kosong_tetap_halaman_valid(repo):
    """Edge: pengguna baru belum punya laporan, daftar kosong bukan galat."""
    # Arrange
    repo.list_reports.return_value = ([], 0)

    # Act
    halaman = service.list_reports(DB, _pengguna(Role.ADMIN), PageParams())

    # Assert
    assert (halaman.items, halaman.total) == ([], 0)


# --- Buka ulang satu laporan (GET /reports/{id}) -----------------------------


def test_get_report_merakit_hasil_beku_tanpa_menghitung_ulang(repo):
    """Positif: angka yang dikembalikan persis angka tersimpan, diurutkan per sesi (D1, D3)."""
    # Arrange
    laporan = _laporan()
    repo.get_report.return_value = laporan
    repo.contents.return_value = _isi(laporan)

    # Act
    detail = service.get_report(DB, laporan.id, _pengguna())

    # Assert
    repo.contents.assert_called_once_with(DB, laporan)
    assert detail.rubric_version_no == 4
    assert [s.product_label for s in detail.sessions] == ["Gemini", "Justisio"]
    gemini = detail.sessions[0].results[0]
    assert (gemini.rank, gemini.det_score, gemini.llm_score) == (
        1,
        Decimal("0.75"),
        Decimal("0.50"),
    )
    assert detail.case_basis[0].model_dump() == {
        "case_code": "UAT-PHK-001",
        "case_version_no": 2,
        "split_tag": SplitTag.TEST,
    }


def test_get_report_hasil_hanya_masuk_ke_sesinya_sendiri(repo):
    """Positif: hasil Dev + Test (tiga baris per sesi) tidak tertukar antar produk."""
    # Arrange
    laporan = _laporan(score_scope=ScoreScope.DEV_TEST, has_dev_scores=True)
    hasil = [_hasil(laporan, SESI_A, 1, score_split=split) for split in ScoreSplit]
    repo.get_report.return_value = laporan
    repo.contents.return_value = _isi(laporan, results=[*hasil, _hasil(laporan, SESI_B, 2)])

    # Act
    detail = service.get_report(DB, laporan.id, _pengguna(Role.ADMIN))

    # Assert
    assert [r.score_split for r in detail.sessions[0].results] == list(ScoreSplit)
    assert len(detail.sessions[1].results) == 1


def test_get_report_membaca_penanda_dan_uji_coba(repo):
    """Positif: penanda D2 dari JSONB dan kolom is_trial ikut terbaca (AC label dan penanda)."""
    # Arrange
    laporan = _laporan()
    sesi = _sesi(
        laporan,
        SESI_A,
        "Justisio",
        measurement_kind=MeasurementKind.MANUAL,
        is_trial=True,
        markers=[{"code": "refusals", "detail": "2"}, "repetitions_differ"],
    )
    repo.get_report.return_value = laporan
    repo.contents.return_value = _isi(laporan, sessions=[sesi], results=[])

    # Act
    detail = service.get_report(DB, laporan.id, _pengguna())

    # Assert
    hasil = detail.sessions[0]
    assert (hasil.measurement_kind, hasil.is_trial) == (MeasurementKind.MANUAL, True)
    # Edge: penanda berbentuk string polos tetap terbaca sebagai kode.
    assert [(m.code, m.detail) for m in hasil.markers] == [
        ("refusals", "2"),
        ("repetitions_differ", None),
    ]


def test_get_report_tidak_ada_ditolak_404(repo):
    """Negatif: id laporan yang tidak ada."""
    # Arrange
    repo.get_report.return_value = None

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        service.get_report(DB, uuid.uuid4(), _pengguna())
    assert galat.value.code == service.REPORT_NOT_FOUND
    repo.contents.assert_not_called()


@pytest.mark.parametrize(
    ("role", "ubah"),
    [
        # Negatif: laporan tersimpan milik orang lain (D9, hanya Admin).
        (Role.CONTRIBUTOR, {"created_by": ORANG_LAIN}),
        # Negatif: laporan skor dev untuk Viewer (D9 aturan umum 3).
        (Role.VIEWER, {"score_scope": ScoreScope.DEV, "has_dev_scores": True}),
    ],
)
def test_get_report_yang_tidak_boleh_dibaca_disamarkan_sebagai_404(repo, role, ubah):
    """Negatif: laporan yang tidak boleh dibaca dijawab 404, bukan 403.

    Dengan begitu pengguna tidak bisa menebak laporan tersimpan orang lain ada atau tidak,
    dan isi hasil beku tidak pernah dibaca dari database.
    """
    # Arrange
    laporan = _laporan(**ubah)
    repo.get_report.return_value = laporan

    # Act / Assert
    with pytest.raises(NotFoundError):
        service.get_report(DB, laporan.id, _pengguna(role))
    repo.contents.assert_not_called()
