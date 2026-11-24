"""Unit test penelusuran angka laporan (PBI-16 SCRUM-326, diagram D6 langkah 1-7).

Repository di-mock dengan create_autospec, dan run penilaian memakai MemoryGradingStore
(store pengganti resmi dari runs.session_grade). Jadi yang diuji hanya logika
drilldown: siapa boleh membuka angka mana, kasus mana yang ada di balik satu angka,
skor dari run yang beku, dan penyamaran per peran.

Sumber aturan:
- AC1: setiap angka bisa dibuka menjadi daftar kasus dan versi kasusnya.
- D9 Sprint 4: daftar kasus dan skor terbuka untuk semua peran, isi kasus tidak.
- D9 aturan umum 2: kode kasus test disamarkan untuk Developer AI.
- AC2: satu kasus dibuka sampai jawaban asli, skor, alasan, versi rubrik; gagal teknis
  "tidak dinilai"; jawaban manual dengan label, pengisi, dan tanggal.
- AC3 dan D9 Sprint 4: isi kasus test hanya Admin dan Kontributor, isi kasus dev hanya
  Admin dan Developer AI. Peran lain 403 dengan email untuk meminta isi kasus.
- AC4: bukti jawaban manual dibuka lewat service bukti yang sudah ada, hanya untuk kasus
  di balik angka yang ditelusuri.

Label: Positif = jalur normal, Negatif = harus ditolak atau disembunyikan,
Edge = data batas atau tidak lengkap.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec

import pytest

from app.modules.auth import service as auth_service
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
from app.modules.reports.repository import CaseBasisRow, ManualAnswerFacts
from app.modules.runs.manual_evidence import EvidenceDownload, ManualEvidenceService
from app.modules.runs.models import AttemptSource, AttemptStatus
from app.modules.runs.schemas import (
    AttemptGradeResult,
    CaseGradeResult,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import DbGradingStore, MemoryGradingStore, RunCaseScores
from app.shared.exceptions import ForbiddenError, NotFoundError
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


def _kasus_dinilai(case_id, kode, tag, det, llm, attempts=()) -> CaseGradeResult:
    return CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=case_id,
        case_code=kode,
        title=f"Judul {kode}",
        split_tag=tag,
        category="Ketenagakerjaan",
        question=f"Pertanyaan {kode}",
        scores=ScorePair(deterministic=Decimal(det), quality=Decimal(llm), format=Decimal("0.90")),
        attempts=list(attempts),
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


@pytest.mark.parametrize("role", list(Role))
def test_daftar_kasus_hanya_membaca_skor_bukan_jawaban(repo, role):
    """Negatif: daftar kasus terbuka untuk semua peran, jadi tidak pernah membaca soal atau
    jawaban asli; hanya skor per kasus dari run beku (D6 langkah 6, review #149)."""
    # Arrange
    report = _laporan(score_scope=ScoreScope.TEST, has_dev_scores=False)
    angka = _angka(report, ScoreSplit.TEST)
    _siapkan(repo, report, angka)
    runs = create_autospec(DbGradingStore, instance=True)
    runs.get_case_scores.return_value = RunCaseScores("v4", {})

    # Act
    drilldown.list_cases(DB, report.id, angka.id, _pengguna(role), runs=runs)

    # Assert
    runs.get_case_scores.assert_called_once_with(SESI, RUN)
    runs.get_grading_case.assert_not_called()
    runs.get_grading_run.assert_not_called()


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


# --- Satu kasus sampai jawaban asli (AC2, AC3) -----------------------------------

MANUAL_DINILAI = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
MANUAL_GAGAL = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
PENGISI = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
DIJAWAB = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture
def nama(monkeypatch):
    """Nama pengisi dari modul auth, ditiru supaya tes tidak bergantung tabel users."""
    tiruan = create_autospec(auth_service, instance=True)
    tiruan.user_names.return_value = {PENGISI: "Kontributor Satu"}
    monkeypatch.setattr(drilldown, "auth_service", tiruan)
    return tiruan


def _percobaan(attempt_id, nomor, *, gagal=False) -> AttemptGradeResult:
    """Percobaan manual: yang gagal teknis tidak punya skor, sama seperti hasil session_grade."""
    return AttemptGradeResult(
        attempt_id=attempt_id,
        repetition_no=nomor,
        status=AttemptStatus.TECHNICAL_FAILURE if gagal else AttemptStatus.SUCCEEDED,
        source=AttemptSource.MANUAL,
        is_technical_disruption=gagal,
        score=None if gagal else ScorePair(deterministic=Decimal("0.80"), quality=Decimal("0.75")),
        answer_text=None if gagal else "Jawaban asli produk tentang PHK.",
        explanation=None if gagal else "Pasal 151 dikutip dengan benar.",
        reasons=[] if gagal else ["CITATION_OK"],
    )


def _siapkan_kasus(repo, report: Report, angka: ReportResult) -> MemoryGradingStore:
    """Kasus test dengan dua percobaan manual (satu gagal teknis) dan kasus dev tanpa percobaan."""
    _siapkan(repo, report, angka)
    repo.manual_answer_facts.return_value = {
        MANUAL_DINILAI: ManualAnswerFacts(PENGISI, DIJAWAB),
        MANUAL_GAGAL: ManualAnswerFacts(None, None),
    }
    return _store(
        _kasus_dinilai(
            KASUS_TEST,
            "UAT-PHK-001",
            SplitTag.TEST,
            "0.80",
            "0.75",
            attempts=[_percobaan(MANUAL_DINILAI, 1), _percobaan(MANUAL_GAGAL, 2, gagal=True)],
        ),
        _kasus_dinilai(KASUS_DEV, "DEV-PHK-009", SplitTag.DEV, "0.40", "0.55"),
    )


def test_kasus_dibuka_sampai_jawaban_asli_skor_alasan_dan_rubrik(repo, nama):
    """Positif: Admin membaca jawaban asli, skor, alasan, dan versi rubrik tiap percobaan (AC2)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan_kasus(repo, report, angka)

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    assert (kasus.case_code, kasus.case_version_no, kasus.question, kasus.rubric_version) == (
        "UAT-PHK-001",
        2,
        "Pertanyaan UAT-PHK-001",
        "v4",
    )
    pertama = kasus.attempts[0]
    assert (pertama.graded, pertama.det_score, pertama.llm_score) == (
        True,
        Decimal("0.80"),
        Decimal("0.75"),
    )
    assert (pertama.answer_text, pertama.explanation, pertama.reasons) == (
        "Jawaban asli produk tentang PHK.",
        "Pasal 151 dikutip dengan benar.",
        ["CITATION_OK"],
    )


def test_jawaban_manual_membawa_label_pengisi_dan_tanggal(repo, nama):
    """Positif: jawaban manual diberi label, nama pengisi, dan tanggal jawaban diperoleh (AC2)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan_kasus(repo, report, angka)

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    pertama = kasus.attempts[0]
    assert (pertama.source, pertama.filled_by_name, pertama.answered_at) == (
        AttemptSource.MANUAL,
        "Kontributor Satu",
        DIJAWAB,
    )
    repo.manual_answer_facts.assert_called_once_with(DB, [MANUAL_DINILAI, MANUAL_GAGAL])
    nama.user_names.assert_called_once_with(DB, {PENGISI})


def test_percobaan_gagal_teknis_ditandai_tidak_dinilai(repo, nama):
    """Edge: gagal teknis tidak punya skor dan ditandai tidak dinilai, bukan skor 0 (AC2)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan_kasus(repo, report, angka)

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    gagal = kasus.attempts[1]
    assert (gagal.graded, gagal.det_score, gagal.llm_score, gagal.filled_by_name) == (
        False,
        None,
        None,
        None,
    )


# Laporan test: satu-satunya jenis laporan yang bisa dibaca Viewer dan Kontributor (D9).
_LAPORAN_TEST = {"score_scope": ScoreScope.TEST, "has_dev_scores": False}


@pytest.mark.parametrize(
    ("role", "case_id", "laporan", "split"),
    [
        # Positif: Kontributor membuka isi kasus test di laporan test (AC3).
        (Role.CONTRIBUTOR, KASUS_TEST, _LAPORAN_TEST, ScoreSplit.TEST),
        # Positif: Developer AI membuka isi kasus dev di laporan Dev + Test (D9 Sprint 4).
        (Role.DEVELOPER_AI, KASUS_DEV, {}, ScoreSplit.COMBINED),
    ],
)
def test_peran_yang_berhak_membuka_isi_kasus(repo, nama, role, case_id, laporan, split):
    """Positif: hak membuka isi mengikuti tabel penelusuran di runs/case_visibility.py."""
    # Arrange
    report = _laporan(**laporan)
    angka = _angka(report, split)
    store = _siapkan_kasus(repo, report, angka)

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, case_id, _pengguna(role), runs=store)

    # Assert
    assert kasus.case_id == case_id


@pytest.mark.parametrize(
    ("role", "laporan", "split"),
    [
        # Negatif: Viewer murni tidak melihat isi kasus sama sekali (AC3).
        (Role.VIEWER, _LAPORAN_TEST, ScoreSplit.TEST),
        # Negatif: Developer AI tidak membuka isi kasus test (D9 aturan 2).
        (Role.DEVELOPER_AI, {}, ScoreSplit.COMBINED),
    ],
)
def test_peran_tanpa_hak_ditolak_403_tanpa_membaca_jawaban(repo, nama, role, laporan, split):
    """Negatif: 403 CASE_CONTENT_FORBIDDEN dengan email kontak, dan run penilaian (yang berisi
    jawaban asli) tidak pernah dibaca (D6 langkah 6).

    Kontributor dan kasus dev tidak diuji di sini: laporan berisi kasus dev sudah 404 untuk
    Kontributor; aturannya diuji di test_case_visibility.py.
    """
    # Arrange
    report = _laporan(**laporan)
    angka = _angka(report, split)
    _siapkan_kasus(repo, report, angka)

    # Act / Assert
    with pytest.raises(ForbiddenError) as galat:
        drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(role))
    assert galat.value.code == drilldown.CASE_CONTENT_FORBIDDEN
    assert "indolegalbench@veritask.ai" in galat.value.message
    repo.grading_run_at.assert_not_called()


@pytest.mark.parametrize(
    ("split", "case_id"),
    [
        # Negatif: kasus dev tidak bisa dibuka lewat angka test.
        (ScoreSplit.TEST, KASUS_DEV),
        # Negatif: kasus yang bukan dasar laporan ini.
        (ScoreSplit.COMBINED, uuid.uuid4()),
    ],
)
def test_kasus_di_luar_angka_ini_dijawab_404(repo, nama, split, case_id):
    """Negatif: hanya kasus yang membentuk angka yang diklik yang bisa dibuka."""
    # Arrange
    report = _laporan()
    angka = _angka(report, split)
    _siapkan_kasus(repo, report, angka)

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.case_detail(DB, report.id, angka.id, case_id, _pengguna())
    assert galat.value.code == drilldown.REPORT_CASE_NOT_FOUND


def test_kasus_dasar_yang_tidak_ada_di_run_dijawab_404(repo, nama):
    """Edge: data rusak (kasus dasar tidak dinilai di run beku) tidak dijawab dengan isi kosong."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    _siapkan_kasus(repo, report, angka)

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=_store())
    assert galat.value.code == drilldown.REPORT_GRADES_NOT_FOUND


def test_pengisi_yang_sudah_tidak_ada_namanya_kosong(repo, nama):
    """Edge: akun pengisi yang sudah dihapus tidak menggagalkan penelusuran; namanya null."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    store = _siapkan_kasus(repo, report, angka)
    nama.user_names.return_value = {}

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    assert (kasus.attempts[0].filled_by_name, kasus.attempts[0].answered_at) == (None, DIJAWAB)


# --- Bukti jawaban manual (AC4) ---------------------------------------------------

ITEM = uuid.UUID("00000000-0000-0000-0000-0000000000a9")
BUKTI = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
def bukti():
    """Service bukti SCRUM-233 tiruan dengan tanda tangan asli."""
    tiruan = create_autospec(ManualEvidenceService, instance=True)
    tiruan.list.return_value = []
    tiruan.download.return_value = EvidenceDownload(b"%PDF-", "application/pdf", "bukti.pdf")
    return tiruan


def _laporan_test_untuk_kontributor(repo) -> tuple[Report, ReportResult]:
    report = _laporan(score_scope=ScoreScope.TEST, has_dev_scores=False, status="published")
    angka = _angka(report, ScoreSplit.TEST)
    _siapkan(repo, report, angka)
    return report, angka


def test_daftar_bukti_lewat_sesi_dan_item_kasus_yang_ditelusuri(repo, bukti):
    """Positif: Kontributor membuka bukti jawaban manual kasus test di laporan terbit (AC4)."""
    # Arrange
    report, angka = _laporan_test_untuk_kontributor(repo)
    repo.snapshot_item_id.return_value = ITEM

    # Act
    drilldown.list_evidence(
        DB, report.id, angka.id, KASUS_TEST, 2, _pengguna(Role.CONTRIBUTOR), bukti
    )

    # Assert
    repo.snapshot_item_id.assert_called_once_with(DB, SESI, KASUS_TEST)
    bukti.list.assert_called_once_with(SESI, ITEM, 2, role=Role.CONTRIBUTOR)


def test_daftar_bukti_kasus_tanpa_item_snapshot_dijawab_404(repo, bukti):
    """Edge: kasus dasar yang tidak ada di snapshot sesi (data rusak) tidak memanggil service."""
    # Arrange
    report, angka = _laporan_test_untuk_kontributor(repo)
    repo.snapshot_item_id.return_value = None

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.list_evidence(DB, report.id, angka.id, KASUS_TEST, 1, _pengguna(), bukti)
    assert galat.value.code == drilldown.REPORT_CASE_NOT_FOUND
    bukti.list.assert_not_called()


def test_bukti_untuk_peran_tanpa_hak_isi_ditolak_403(repo, bukti):
    """Negatif: Viewer yang lolos route pun tetap ditolak di service, sebelum bukti dibaca."""
    # Arrange
    report, angka = _laporan_test_untuk_kontributor(repo)

    # Act / Assert
    with pytest.raises(ForbiddenError):
        drilldown.list_evidence(
            DB, report.id, angka.id, KASUS_TEST, 1, _pengguna(Role.VIEWER), bukti
        )
    bukti.list.assert_not_called()


def test_unduh_bukti_milik_kasus_yang_ditelusuri(repo, bukti):
    """Positif: berkas bukti diunduh lewat service bukti dengan sesi di balik angka (AC4)."""
    # Arrange
    report, angka = _laporan_test_untuk_kontributor(repo)
    repo.evidence_case_id.return_value = KASUS_TEST

    # Act
    berkas = drilldown.evidence_file(
        DB, report.id, angka.id, KASUS_TEST, BUKTI, _pengguna(Role.CONTRIBUTOR), bukti
    )

    # Assert
    assert berkas.file_name == "bukti.pdf"
    bukti.download.assert_called_once_with(SESI, BUKTI, role=Role.CONTRIBUTOR)


@pytest.mark.parametrize(
    "pemilik",
    [
        # Negatif: bukti kasus lain di sesi yang sama.
        KASUS_DEV,
        # Negatif: bukti bukan milik sesi di balik angka ini.
        None,
    ],
)
def test_unduh_bukti_kasus_lain_dijawab_404(repo, bukti, pemilik):
    """Negatif: laporan ini tidak bisa dipakai membuka bukti di luar kasus yang ditelusuri."""
    # Arrange
    report, angka = _laporan_test_untuk_kontributor(repo)
    repo.evidence_case_id.return_value = pemilik

    # Act / Assert
    with pytest.raises(NotFoundError) as galat:
        drilldown.evidence_file(DB, report.id, angka.id, KASUS_TEST, BUKTI, _pengguna(), bukti)
    assert galat.value.code == drilldown.REPORT_EVIDENCE_NOT_FOUND
    bukti.download.assert_not_called()


# --- Produk lain pada kasus yang sama (PBI-16 SCRUM-370) -------------------------

SESI_B = uuid.UUID("00000000-0000-0000-0000-00000000005b")
SESI_C = uuid.UUID("00000000-0000-0000-0000-00000000005c")
RUN_B = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
RUN_C = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
HASIL_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
HASIL_C = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
PERCOBAAN_B = uuid.UUID("00000000-0000-0000-0000-0000000000e3")
PERCOBAAN_B2 = uuid.UUID("00000000-0000-0000-0000-0000000000e4")
PERCOBAAN_C = uuid.UUID("00000000-0000-0000-0000-0000000000e5")


def _baris_lain(session_id, result_id, label, *, kind=MeasurementKind.AUTOMATIC, uji_coba=False):
    return repository.OtherProductRow(result_id, session_id, label, kind, uji_coba)


def _percobaan_lain(
    attempt_id,
    nomor,
    *,
    status=AttemptStatus.SUCCEEDED,
    gagal=False,
    source=AttemptSource.AUTOMATIC,
) -> AttemptGradeResult:
    """Percobaan produk lain; yang gagal atau belum jalan tidak punya skor dan jawaban."""
    dinilai = status is AttemptStatus.SUCCEEDED and not gagal
    return AttemptGradeResult(
        attempt_id=attempt_id,
        repetition_no=nomor,
        status=AttemptStatus.TECHNICAL_FAILURE if gagal else status,
        source=source,
        is_technical_disruption=gagal,
        score=ScorePair(deterministic=Decimal("0.60"), quality=Decimal("0.50"))
        if dinilai
        else None,
        answer_text="Jawaban produk lain." if dinilai else None,
        explanation="Pasal 151 disebut sebagian." if dinilai else None,
        reasons=["CITATION_OK"] if dinilai else [],
    )


def _kasus_lain(*attempts, det="0.60", llm="0.50") -> CaseGradeResult:
    return _kasus_dinilai(KASUS_TEST, "UAT-PHK-001", SplitTag.TEST, det, llm, attempts=attempts)


def _tambah_run(store, session_id, run_id, rubrik, *cases) -> None:
    kosong = ScorePair()
    store.save_grading_run(
        SessionGradingResponse(
            grading_run_id=run_id,
            session_id=session_id,
            rubric_version=rubrik,
            kind="initial",
            status="completed",
            triggered_by=PEMBUAT,
            triggered_at=datetime(2026, 11, 30, tzinfo=UTC),
            scores=ThreeScores(dev=kosong, test=kosong, combined=kosong),
            cases=list(cases),
        )
    )


def _perbandingan(repo, report, angka, lain, isi) -> MemoryGradingStore:
    """Produk utama dengan kasus dan percobaan manual, ditambah produk lain.

    `isi` memetakan session_id produk lain ke (run_id, versi rubrik, kasus yang dinilai).
    Sesi tanpa entri tidak punya run beku sama sekali.
    """
    store = _siapkan_kasus(repo, report, angka)
    repo.other_product_results.return_value = lain
    run_beku = {SESI: RUN}
    for session_id, (run_id, rubrik, kasus) in isi.items():
        run_beku[session_id] = run_id
        _tambah_run(store, session_id, run_id, rubrik, *kasus)
    repo.grading_run_at.side_effect = lambda db, session_id, at: run_beku.get(session_id)
    return store


def _produk_b(*kasus, rubrik="v5") -> tuple[list, dict]:
    return [_baris_lain(SESI_B, HASIL_B, "Produk B")], {SESI_B: (RUN_B, rubrik, list(kasus))}


def _detail_lain(repo, nama, lain, isi, role=Role.ADMIN, **laporan):
    report = _laporan(**laporan)
    angka = _angka(report, ScoreSplit.TEST if laporan else ScoreSplit.COMBINED)
    store = _perbandingan(repo, report, angka, lain, isi)
    return drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(role), runs=store)


def test_produk_lain_tampil_dengan_jawaban_skor_alasan_dan_rubriknya(repo, nama):
    """Positif: tiap produk lain membawa jawaban asli, skor, alasan, dan versi rubrik sendiri."""
    # Arrange
    lain = [
        _baris_lain(SESI_B, HASIL_B, "Produk B"),
        _baris_lain(SESI_C, HASIL_C, "Produk C", kind=MeasurementKind.MANUAL, uji_coba=True),
    ]
    isi = {
        SESI_B: (RUN_B, "v5", [_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1))]),
        SESI_C: (
            RUN_C,
            "v3",
            [_kasus_lain(_percobaan_lain(PERCOBAAN_C, 1), det="0.90", llm="0.85")],
        ),
    }

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    b, c = kasus.other_products
    assert (b.result_id, b.product_label, b.status, b.rubric_version) == (
        HASIL_B,
        "Produk B",
        "answered",
        "v5",
    )
    assert (b.det_score, b.llm_score) == (Decimal("0.60"), Decimal("0.50"))
    assert (b.attempts[0].answer_text, b.attempts[0].reasons, b.attempts[0].explanation) == (
        "Jawaban produk lain.",
        ["CITATION_OK"],
        "Pasal 151 disebut sebagian.",
    )
    assert (c.product_label, c.measurement_kind, c.is_trial, c.rubric_version) == (
        "Produk C",
        MeasurementKind.MANUAL,
        True,
        "v3",
    )
    assert (c.det_score, c.llm_score) == (Decimal("0.90"), Decimal("0.85"))


def test_produk_utama_tetap_terbaca_bersama_perbandingan(repo, nama):
    """Positif: menambah perbandingan tidak mengubah isi kasus produk yang sedang dibuka."""
    # Arrange
    lain, isi = _produk_b(_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1)))

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    assert (kasus.product_label, kasus.rubric_version, kasus.det_score) == (
        "Justisio",
        "v4",
        Decimal("0.80"),
    )
    assert [a.answer_text for a in kasus.attempts][0] == "Jawaban asli produk tentang PHK."


def test_produk_yang_tidak_punya_kasus_itu_ditandai_tidak_ada_jawaban(repo, nama):
    """Edge: produk B tidak punya kasus ini di run-nya, jadi bukan "tidak dinilai" dan bukan 404."""
    # Arrange
    lain, isi = _produk_b(
        _kasus_dinilai(KASUS_DEV, "DEV-PHK-009", SplitTag.DEV, "0.40", "0.55"),
    )

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    (b,) = kasus.other_products
    assert (b.status, b.rubric_version, b.det_score, b.llm_score, b.attempts) == (
        "no_answer",
        None,
        None,
        None,
        [],
    )


@pytest.mark.parametrize(
    "percobaan",
    [
        # Edge: kasus ada di run tetapi tidak ada percobaan sama sekali.
        [],
        # Edge: percobaan ada tetapi belum jalan, jadi produk belum menjawab.
        [(AttemptStatus.PENDING, False)],
        [(AttemptStatus.RUNNING, False), (AttemptStatus.PENDING, False)],
    ],
)
def test_produk_tanpa_percobaan_yang_selesai_ditandai_tidak_ada_jawaban(repo, nama, percobaan):
    """Edge: tanpa percobaan yang selesai, produk itu tidak menjawab (bukan "tidak dinilai")."""
    # Arrange
    attempts = [
        _percobaan_lain(uuid.uuid4(), nomor, status=status, gagal=gagal)
        for nomor, (status, gagal) in enumerate(percobaan, start=1)
    ]
    lain, isi = _produk_b(_kasus_lain(*attempts))

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    (b,) = kasus.other_products
    assert (b.status, b.rubric_version, len(b.attempts)) == ("no_answer", "v5", len(attempts))


def test_semua_percobaan_gagal_teknis_ditandai_tidak_dinilai(repo, nama):
    """Positif: percobaan gagal teknis tidak diberi skor 0, produknya ditandai "tidak dinilai"."""
    # Arrange
    lain, isi = _produk_b(
        _kasus_lain(
            _percobaan_lain(PERCOBAAN_B, 1, gagal=True),
            _percobaan_lain(PERCOBAAN_B2, 2, gagal=True),
        )
    )

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    (b,) = kasus.other_products
    assert b.status == "not_graded"
    assert [(a.graded, a.det_score, a.answer_text) for a in b.attempts] == [
        (False, None, None),
        (False, None, None),
    ]


def test_percobaan_campuran_tetap_dinilai_dengan_percobaan_gagal_terlihat(repo, nama):
    """Edge: satu percobaan gagal dan satu dinilai, produk tetap dinilai dan keduanya terbaca."""
    # Arrange
    lain, isi = _produk_b(
        _kasus_lain(
            _percobaan_lain(PERCOBAAN_B, 1, gagal=True),
            _percobaan_lain(PERCOBAAN_B2, 2),
        )
    )

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    (b,) = kasus.other_products
    assert b.status == "answered"
    assert [a.graded for a in b.attempts] == [False, True]


def test_jawaban_manual_produk_lain_membawa_label_pengisi_dan_tanggal(repo, nama):
    """Positif: jawaban manual produk lain diperlakukan sama seperti produk utama (AC2 PBI-16)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    lain, isi = _produk_b(
        _kasus_lain(_percobaan_lain(PERCOBAAN_B, 1, source=AttemptSource.MANUAL)),
    )
    store = _perbandingan(repo, report, angka, lain, isi)
    repo.manual_answer_facts.return_value = {PERCOBAAN_B: ManualAnswerFacts(PENGISI, DIJAWAB)}

    # Act
    kasus = drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    (percobaan,) = kasus.other_products[0].attempts
    assert (percobaan.source, percobaan.filled_by_name, percobaan.answered_at) == (
        AttemptSource.MANUAL,
        "Kontributor Satu",
        DIJAWAB,
    )


def test_laporan_satu_produk_tidak_punya_produk_lain(repo, nama):
    """Edge: tanpa produk lain daftar perbandingan kosong, dan detail kasus tetap terbaca."""
    # Act
    kasus = _detail_lain(repo, nama, [], {})

    # Assert
    assert kasus.other_products == []
    assert kasus.case_code == "UAT-PHK-001"


def test_tiap_produk_lain_dibaca_dari_run_beku_sesinya_sendiri(repo, nama):
    """Positif: run yang dipakai tiap produk adalah run pada waktu laporan dibuat (D1)."""
    # Arrange
    report = _laporan()
    angka = _angka(report)
    lain = [_baris_lain(SESI_B, HASIL_B, "Produk B"), _baris_lain(SESI_C, HASIL_C, "Produk C")]
    isi = {
        SESI_B: (RUN_B, "v5", [_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1))]),
        SESI_C: (RUN_C, "v3", [_kasus_lain(_percobaan_lain(PERCOBAAN_C, 1))]),
    }
    store = _perbandingan(repo, report, angka, lain, isi)

    # Act
    drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(), runs=store)

    # Assert
    repo.grading_run_at.assert_any_call(DB, SESI_B, report.created_at)
    repo.grading_run_at.assert_any_call(DB, SESI_C, report.created_at)
    repo.other_product_results.assert_called_once_with(DB, report.id, angka)


def test_produk_lain_tanpa_run_beku_dilewati_tanpa_menggagalkan_detail(repo, nama):
    """Edge: data rusak pada satu produk lain tidak membuat detail kasus utama menjadi 404."""
    # Arrange
    lain = [_baris_lain(SESI_B, HASIL_B, "Produk B"), _baris_lain(SESI_C, HASIL_C, "Produk C")]
    isi = {SESI_B: (RUN_B, "v5", [_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1))])}

    # Act
    kasus = _detail_lain(repo, nama, lain, isi)

    # Assert
    assert [p.product_label for p in kasus.other_products] == ["Produk B"]
    assert kasus.case_code == "UAT-PHK-001"


@pytest.mark.parametrize(
    ("role", "laporan"),
    [
        # Positif: Admin boleh membuka isi kasus dan jawaban asli (AC3 PBI-16).
        (Role.ADMIN, {}),
        # Positif: Kontributor boleh untuk kasus test.
        (Role.CONTRIBUTOR, _LAPORAN_TEST),
    ],
)
def test_admin_dan_kontributor_menerima_jawaban_produk_lain(repo, nama, role, laporan):
    """Positif: peran yang boleh membuka isi kasus juga menerima jawaban produk lain."""
    # Arrange
    lain, isi = _produk_b(_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1)))

    # Act
    kasus = _detail_lain(repo, nama, lain, isi, role=role, **laporan)

    # Assert
    assert [p.attempts[0].answer_text for p in kasus.other_products] == ["Jawaban produk lain."]


@pytest.mark.parametrize(
    ("role", "laporan", "split"),
    [
        # Negatif: Viewer murni tidak melihat jawaban produk mana pun (AC3 PBI-16).
        (Role.VIEWER, _LAPORAN_TEST, ScoreSplit.TEST),
        # Negatif: Developer AI tidak membuka isi kasus test.
        (Role.DEVELOPER_AI, {}, ScoreSplit.COMBINED),
    ],
)
def test_peran_tanpa_hak_ditolak_sebelum_jawaban_produk_lain_dibaca(
    repo, nama, role, laporan, split
):
    """Negatif: 403 dijatuhkan sebelum produk lain dicari, jadi jawaban mereka tidak dibaca."""
    # Arrange
    report = _laporan(**laporan)
    angka = _angka(report, split)
    lain, isi = _produk_b(_kasus_lain(_percobaan_lain(PERCOBAAN_B, 1)))
    _perbandingan(repo, report, angka, lain, isi)
    runs = create_autospec(DbGradingStore, instance=True)

    # Act / Assert
    with pytest.raises(ForbiddenError) as galat:
        drilldown.case_detail(DB, report.id, angka.id, KASUS_TEST, _pengguna(role), runs=runs)
    assert galat.value.code == drilldown.CASE_CONTENT_FORBIDDEN
    repo.other_product_results.assert_not_called()
    runs.get_grading_case.assert_not_called()
