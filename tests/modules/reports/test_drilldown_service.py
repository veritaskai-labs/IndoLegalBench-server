"""Unit test penelusuran angka laporan (PBI-16 SCRUM-326, diagram D6 langkah 1-7).

Repository di-mock dengan create_autospec, dan run penilaian memakai MemoryGradingStore
(store pengganti resmi dari runs.session_grade). Jadi yang diuji hanya logika
drilldown: siapa boleh membuka angka mana, kasus mana yang ada di balik satu angka,
skor dari run yang beku, dan penyamaran per peran.

Sumber aturan:
- AC1: setiap angka bisa dibuka menjadi daftar kasus dan versi kasusnya.
- D9 Sprint 4: daftar kasus dan skor terbuka untuk semua peran, isi kasus tidak.
- D9 aturan umum 2: kode kasus test disamarkan untuk Developer AI.

Label: Positif = jalur normal, Negatif = harus ditolak atau disembunyikan,
Edge = data batas atau tidak lengkap.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec

import pytest

from app.modules.cases.models import SplitTag
from app.modules.cases.service import MASKED_CASE_CODE
from app.modules.reports import drilldown, history, repository
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
from app.modules.reports.repository import CaseBasisRow
from app.modules.runs.schemas import (
    CaseGradeResult,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import MemoryGradingStore
from app.shared.exceptions import NotFoundError
from app.shared.security import CurrentUser, Role

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
SESI = uuid.UUID("00000000-0000-0000-0000-00000000005a")
RUN = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
KASUS_TEST = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
KASUS_DEV = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
DB = object()  # Sesi database palsu: drilldown hanya meneruskannya ke repository.


@pytest.fixture
def repo(monkeypatch):
    """Satu repository tiruan untuk drilldown dan history.readable_report sekaligus."""
    tiruan = create_autospec(repository, instance=True)
    monkeypatch.setattr(drilldown, "repository", tiruan)
    monkeypatch.setattr(history, "repository", tiruan)
    return tiruan


def _pengguna(role=Role.ADMIN, user_id=PEMBUAT) -> CurrentUser:
    return CurrentUser(user_id=user_id, name="Uji", email="uji@veritask.test", role=role)


def _laporan(**ubah) -> Report:
    isi = {
        "id": uuid.uuid4(),
        "title": "Laporan Desember",
        "score_scope": ScoreScope.DEV_TEST,
        "has_dev_scores": True,
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


def _angka(report: Report, split=ScoreSplit.COMBINED) -> ReportResult:
    return ReportResult(id=uuid.uuid4(), report_id=report.id, session_id=SESI, score_split=split)


def _kasus_dinilai(case_id, kode, tag, det, llm) -> CaseGradeResult:
    return CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=case_id,
        case_code=kode,
        title=f"Judul {kode}",
        split_tag=tag,
        scores=ScorePair(deterministic=Decimal(det), quality=Decimal(llm), format=Decimal("0.90")),
    )


def _store(*cases: CaseGradeResult) -> MemoryGradingStore:
    """Run beku sesi ini dengan versi rubrik v4; format sengaja beda supaya tidak tertukar."""
    kosong = ScorePair()
    store = MemoryGradingStore()
    store.save_grading_run(
        SessionGradingResponse(
            grading_run_id=RUN,
            session_id=SESI,
            rubric_version="v4",
            kind="initial",
            status="completed",
            triggered_by=PEMBUAT,
            triggered_at=datetime(2026, 11, 30, tzinfo=UTC),
            scores=ThreeScores(dev=kosong, test=kosong, combined=kosong),
            cases=list(cases),
        )
    )
    return store


def _siapkan(repo, report: Report, angka: ReportResult) -> MemoryGradingStore:
    """Laporan dengan satu kasus test dan satu kasus dev, keduanya sudah dinilai."""
    repo.get_report.return_value = report
    repo.get_result.return_value = angka
    repo.get_report_session.return_value = ReportSession(
        report_id=report.id,
        session_id=SESI,
        product_label="Justisio",
        measurement_kind=MeasurementKind.MANUAL,
    )
    repo.grading_run_at.return_value = RUN
    repo.case_basis.return_value = [
        CaseBasisRow("DEV-PHK-009", 1, SplitTag.DEV, KASUS_DEV),
        CaseBasisRow("UAT-PHK-001", 2, SplitTag.TEST, KASUS_TEST),
    ]
    return _store(
        _kasus_dinilai(KASUS_TEST, "UAT-PHK-001", SplitTag.TEST, "0.80", "0.75"),
        _kasus_dinilai(KASUS_DEV, "DEV-PHK-009", SplitTag.DEV, "0.40", "0.55"),
    )


# --- Kasus di balik satu angka (AC1) -----------------------------------------


def test_angka_gabungan_dibuka_menjadi_kasus_versi_dan_skornya(repo):
    """Positif: Admin membuka angka gabungan dan mendapat semua kasus dasar beserta skornya."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan(repo, report, angka)

    # Act
    hasil = drilldown.list_cases(DB, report.id, angka.id, _pengguna(), runs=store)

    # Assert
    assert (hasil.result_id, hasil.product_label, hasil.rubric_version) == (
        angka.id,
        "Justisio",
        "v4",
    )
    assert [(k.case_code, k.case_version_no, k.det_score, k.llm_score) for k in hasil.cases] == [
        ("DEV-PHK-009", 1, Decimal("0.40"), Decimal("0.55")),
        ("UAT-PHK-001", 2, Decimal("0.80"), Decimal("0.75")),
    ]
    assert all(k.content_visible for k in hasil.cases)


@pytest.mark.parametrize(
    ("split", "kode"),
    [
        # Positif: angka test hanya dibentuk kasus test, angka dev hanya kasus dev (D1).
        (ScoreSplit.TEST, ["UAT-PHK-001"]),
        (ScoreSplit.DEV, ["DEV-PHK-009"]),
    ],
)
def test_angka_per_jenis_skor_hanya_berisi_kasusnya(repo, split, kode):
    """Positif: kasus yang tampil sama dengan kasus yang membentuk angka yang diklik."""
    # Arrange
    report = _laporan()
    angka = _angka(report, split)
    store = _siapkan(repo, report, angka)

    # Act
    hasil = drilldown.list_cases(DB, report.id, angka.id, _pengguna(), runs=store)

    # Assert
    assert [k.case_code for k in hasil.cases] == kode
    assert hasil.score_split is split


def test_skor_dibaca_dari_run_yang_dipakai_laporan(repo):
    """Positif: run yang dibaca adalah run beku pada waktu laporan dibuat (D1)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan(repo, report, angka)

    # Act
    drilldown.list_cases(DB, report.id, angka.id, _pengguna(), runs=store)

    # Assert
    repo.grading_run_at.assert_called_once_with(DB, SESI, report.created_at)


def test_developer_ai_kode_kasus_test_disamarkan_tetapi_skornya_tampil(repo):
    """Negatif dan positif: D6 "untuk kasus test hanya kode dan skor"; kodenya disamarkan
    seperti di laporan (D9 aturan 2), isinya tidak bisa dibuka."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan(repo, report, angka)

    # Act
    hasil = drilldown.list_cases(DB, report.id, angka.id, _pengguna(Role.DEVELOPER_AI), runs=store)

    # Assert
    test = next(k for k in hasil.cases if k.split_tag is SplitTag.TEST)
    dev = next(k for k in hasil.cases if k.split_tag is SplitTag.DEV)
    assert (test.case_code, test.content_visible, test.det_score) == (
        MASKED_CASE_CODE,
        False,
        Decimal("0.80"),
    )
    assert (dev.case_code, dev.content_visible) == ("DEV-PHK-009", True)


@pytest.mark.parametrize(
    ("role", "terbuka"),
    [
        # Negatif: Viewer murni hanya melihat kode, versi, dan skor (AC3).
        (Role.VIEWER, False),
        # Positif: Kontributor boleh membuka isi kasus test (AC3).
        (Role.CONTRIBUTOR, True),
    ],
)
def test_daftar_kasus_test_untuk_viewer_dan_kontributor(repo, role, terbuka):
    """Positif dan negatif: daftar sama untuk keduanya, hanya hak membuka isinya berbeda."""
    # Arrange
    report = _laporan(score_scope=ScoreScope.TEST, has_dev_scores=False)
    angka = _angka(report, ScoreSplit.TEST)
    store = _siapkan(repo, report, angka)

    # Act
    hasil = drilldown.list_cases(DB, report.id, angka.id, _pengguna(role), runs=store)

    # Assert
    assert [(k.case_code, k.content_visible) for k in hasil.cases] == [("UAT-PHK-001", terbuka)]


def test_kasus_dasar_tanpa_hasil_penilaian_skornya_kosong(repo):
    """Edge: kasus dasar yang tidak ada di run tetap tampil dengan skor kosong, bukan 0."""
    # Arrange
    report = _laporan()
    angka = _angka(report, ScoreSplit.TEST)
    _siapkan(repo, report, angka)

    # Act
    hasil = drilldown.list_cases(DB, report.id, angka.id, _pengguna(), runs=_store())

    # Assert
    assert [(k.det_score, k.llm_score) for k in hasil.cases] == [(None, None)]


# --- Penolakan (404) -------------------------------------------------------------


def test_laporan_yang_tidak_boleh_dibaca_dijawab_404_tanpa_membaca_angka(repo):
    """Negatif: penelusuran memakai aturan baca laporan yang sama dengan riwayat (D9)."""
    # Arrange
    report = _laporan(created_by=ORANG_LAIN)
    repo.get_report.return_value = report

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.list_cases(DB, report.id, uuid.uuid4(), _pengguna(Role.DEVELOPER_AI))
    assert galat.value.code == history.REPORT_NOT_FOUND
    repo.get_result.assert_not_called()


def test_angka_dari_laporan_lain_dijawab_404(repo):
    """Negatif: id angka yang bukan milik laporan ini tidak bisa ditelusuri."""
    # Arrange
    report = _laporan()
    repo.get_report.return_value = report
    repo.get_result.return_value = None

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.list_cases(DB, report.id, uuid.uuid4(), _pengguna())
    assert galat.value.code == drilldown.REPORT_RESULT_NOT_FOUND
    repo.case_basis.assert_not_called()


@pytest.mark.parametrize("run_id", [None, uuid.uuid4()])
def test_run_beku_tidak_ditemukan_dijawab_404(repo, run_id):
    """Edge: data rusak (tidak ada run sebelum laporan, atau run-nya hilang) tidak memakai
    run lain yang angkanya bisa berbeda dari laporan."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan(repo, report, angka)
    repo.grading_run_at.return_value = run_id

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.list_cases(DB, report.id, angka.id, _pengguna(), runs=store)
    assert galat.value.code == drilldown.REPORT_GRADES_NOT_FOUND
