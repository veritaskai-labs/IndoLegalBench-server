"""Run penilaian tanpa satu pun skor. SCRUM-78 SCRUM-269.

PBI-14 AC4: run yang tidak punya skor sama sekali ditandai gagal, bukan "Selesai", dan
tidak menggantikan hasil terbaru yang sudah ada.

Label: Positif = ada skor, Negatif = tidak ada skor, Edge = campuran dan nilai ulang.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.modules.cases.models import SplitTag
from app.modules.grading.models import GradingRun, GradingRunStatus
from app.modules.runs.models import AttemptSource, AttemptStatus
from app.modules.runs.schemas import ScorePair
from app.modules.runs.session_grade import (
    AttemptGradeInput,
    CaseGradeInput,
    get_session_results,
    grade_session,
    grade_session_data,
    list_grading_runs,
    pilih_run_terbaru,
)
from app.shared.security import Role
from tests.modules.runs.test_session_grade import (  # noqa: F401
    _MockJudgeAsker,
    _setup_sesi_lengkap,
    pembuat,
    produk,
)

_BALASAN_PENILAI = (
    "CRITERION: badan hukum\nMET: yes\nCRITERION: pesangon\nMET: yes\n"
    "CRITERION: Conclusion: sah\nMET: yes\n"
)


def _kasus(*, gangguan: bool) -> CaseGradeInput:
    percobaan = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.TECHNICAL_FAILURE if gangguan else AttemptStatus.SUCCEEDED,
        source=AttemptSource.AUTOMATIC,
        answer_text="" if gangguan else "Jawaban valid",
        deterministic_score=None if gangguan else Decimal("0.80"),
        quality_score=None if gangguan else Decimal("4.00"),
        format_score=None if gangguan else Decimal("0.75"),
    )
    return CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="PHK-001",
        title="Kasus",
        split_tag=SplitTag.DEV,
        attempts=[percobaan],
    )


def _nilai(kasus: list[CaseGradeInput]):
    return grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=kasus,
        triggered_by=uuid.uuid4(),
        rubric_version="v1",
        existing_runs=(),
    )


def test_run_tanpa_satu_pun_skor_berstatus_failed_dan_bukan_terbaru():
    """Negatif: semua percobaan gangguan teknis, jadi tidak ada yang dinilai."""
    # Arrange
    kasus = [_kasus(gangguan=True), _kasus(gangguan=True)]

    # Act
    hasil = _nilai(kasus)

    # Assert
    assert hasil.status == "failed"
    assert hasil.is_latest is False
    assert hasil.scores.combined == ScorePair()


def test_run_dengan_satu_skor_tetap_completed():
    """Edge: satu kasus ternilai sudah cukup, kasus gangguan teknis hanya dilewati (AC4)."""
    # Arrange
    kasus = [_kasus(gangguan=True), _kasus(gangguan=False)]

    # Act
    hasil = _nilai(kasus)

    # Assert
    assert hasil.status == "completed"
    assert hasil.is_latest is True


def test_sesi_tanpa_penilai_disimpan_failed_dan_tidak_menjadi_terbaru(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Negatif: run gagal tersimpan di riwayat, tetapi bukan hasil terbaru."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    hasil = grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Assert
    assert hasil.status == "failed"
    baris = db_session.scalars(select(GradingRun).where(GradingRun.session_id == sesi.id)).one()
    assert baris.status == GradingRunStatus.FAILED
    assert baris.is_latest is False
    assert baris.finished_at is not None
    riwayat = list_grading_runs(db_session, sesi.id)
    assert [item.status for item in riwayat] == ["failed"]


def test_nilai_ulang_gagal_tidak_menggeser_hasil_terbaru_yang_baik(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Edge: nilai ulang tanpa penilai tidak boleh menghapus hasil bagus sebelumnya."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    pertama = grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat.id,
        actor_role=Role.ADMIN,
        judge_asker=_MockJudgeAsker(text=_BALASAN_PENILAI),
    )
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    kedua = grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Assert
    assert pertama.status == "completed"
    assert kedua.status == "failed"
    terbaru = db_session.scalars(
        select(GradingRun).where(GradingRun.session_id == sesi.id, GradingRun.is_latest)
    ).one()
    assert terbaru.id == pertama.grading_run_id


def test_hasil_terbaru_tetap_run_yang_bagus_setelah_nilai_ulang_gagal(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Negatif: GET /results tidak boleh menampilkan run gagal menggantikan hasil yang bagus."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    pertama = grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat.id,
        actor_role=Role.ADMIN,
        judge_asker=_MockJudgeAsker(text=_BALASAN_PENILAI),
    )
    sesi.judge_provider_id = None
    db_session.commit()
    grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Act
    hasil = get_session_results(db_session, sesi.id, viewer_role=Role.ADMIN)

    # Assert
    assert hasil.grading_run_id == pertama.grading_run_id
    assert hasil.status == "completed"
    assert hasil.is_latest is True
    assert hasil.scores.dev.quality is not None


def test_hasil_penilaian_pertama_yang_gagal_tetap_dikembalikan(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Edge: tanpa run yang bagus, run gagal tetap tampil supaya alasannya terbaca."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.judge_provider_id = None
    db_session.commit()
    gagal = grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Act
    hasil = get_session_results(db_session, sesi.id, viewer_role=Role.ADMIN)

    # Assert
    assert hasil.grading_run_id == gagal.grading_run_id
    assert hasil.status == "failed"
    assert hasil.cases[0].attempts[0].is_technical_disruption is True


def test_run_yang_dipilih_sebagai_terbaru_mengikuti_is_latest_bukan_waktu():
    """Edge: aturan yang sama dipakai penyimpanan memori dan database."""
    # Arrange
    bagus = _nilai([_kasus(gangguan=False)]).model_copy(update={"is_latest": True})
    gagal = _nilai([_kasus(gangguan=True)])

    # Act
    dipilih = pilih_run_terbaru([bagus, gagal])

    # Assert
    assert dipilih is bagus
    assert pilih_run_terbaru([gagal]) is gagal
    assert pilih_run_terbaru([]) is None
