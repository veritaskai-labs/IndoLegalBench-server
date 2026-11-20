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
