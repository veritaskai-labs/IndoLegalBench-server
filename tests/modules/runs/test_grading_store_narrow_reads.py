"""Bacaan sempit run penilaian untuk penelusuran laporan (PBI-16 SCRUM-326, review #149).

Daftar kasus di balik angka laporan hanya butuh skor per kasus, dan detail kasus hanya butuh
satu kasus. Keduanya dulu membangun ulang seluruh run (semua soal, jawaban, call log, baris
penilai, dan kriteria). Tes di sini memakai SQLite dengan run D6 asli dari grade_session, dan
mengunci bahwa angka dari bacaan sempit sama persis dengan run lengkap, supaya daftar kasus dan
detail kasus tidak pernah berbeda angka dari halaman sesi.
"""

import uuid
from decimal import Decimal

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    SessionStatus,
)
from app.modules.runs.schemas import (
    CaseGradeResult,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import (
    DbGradingStore,
    MemoryGradingStore,
    RunCase,
    RunCaseScores,
    grade_session,
    load_case_grade_inputs,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import (
    _BALASAN_PENILAI,
    _buat_body_kasus,
    _MockJudgeAsker,
    _setup_sesi_lengkap,
)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-sempit@veritask.test", name="Admin Sempit", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Bacaan Sempit")
    db_session.add(item)
    db_session.commit()
    return item


def _item(db_session, sesi, kode: str) -> CaseSnapshotItem:
    return next(
        item
        for item in db_session.query(CaseSnapshotItem)
        .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
        .filter(CaseSnapshot.session_id == sesi.id)
        if item.body["sections"]["case_code"] == kode
    )


def _percobaan(db_session, sesi, item, nomor: int, jawaban: str) -> None:
    db_session.add(
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item.id,
            repetition_no=nomor,
            source=AttemptSource.AUTOMATIC,
            status=AttemptStatus.SUCCEEDED,
            answer_text=jawaban,
            model_version="model-v1",
        )
    )


@pytest.fixture
def run_dinilai(db_session, produk, pembuat):
    """Sesi tiga kasus yang sudah dinilai di D6.

    DEV-01: satu percobaan dinilai dan satu jawaban kosong (gangguan teknis, dilewati).
    TEST-02: dua percobaan dinilai, skornya dirata-rata.
    KOSONG-03: kasus di snapshot tanpa percobaan, jadi tanpa skor.
    """
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    _percobaan(db_session, sesi, _item(db_session, sesi, "DEV-01"), 2, "")
    _percobaan(db_session, sesi, _item(db_session, sesi, "TEST-02"), 2, "Tidak tahu.")
    snapshot = db_session.query(CaseSnapshot).filter_by(session_id=sesi.id).one()
    db_session.add(
        CaseSnapshotItem(
            snapshot_id=snapshot.id,
            case_id=uuid.uuid4(),
            case_version_id=uuid.uuid4(),
            body=_buat_body_kasus("KOSONG-03", "test", "Pertanyaan tanpa jawaban"),
        )
    )
    db_session.commit()
    run = grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat.id,
        actor_role=Role.ADMIN,
        judge_asker=_MockJudgeAsker(text=_BALASAN_PENILAI),
    )
    return sesi, run


# --- DbGradingStore.get_case_scores ----------------------------------------------


def test_skor_per_kasus_sama_dengan_run_lengkap(db_session, run_dinilai):
    """Positif: setiap skor kasus dari bacaan sempit sama dengan run lengkap (review #149)."""
    # Arrange
    sesi, run = run_dinilai
    store = DbGradingStore(db_session)
    lengkap = store.get_grading_run(sesi.id, run.grading_run_id)

    # Act
    sempit = store.get_case_scores(sesi.id, run.grading_run_id)

    # Assert
    assert sempit is not None
    assert sempit.rubric_version == lengkap.rubric_version
    for kasus in lengkap.cases:
        assert sempit.scores.get(kasus.case_id) == kasus.scores, kasus.case_code


def test_gangguan_teknis_dilewati_dan_dua_percobaan_dirata_rata(db_session, run_dinilai):
    """Edge: jawaban kosong tidak dihitung 0, dan kasus tanpa percobaan tidak punya skor."""
    # Arrange: pembanding dibaca ulang dari D6, sumber yang sama dengan angka laporan.
    sesi, run = run_dinilai
    store = DbGradingStore(db_session)
    kasus = {c.case_code: c for c in store.get_grading_run(sesi.id, run.grading_run_id).cases}
    dev_dinilai, dev_kosong = kasus["DEV-01"].attempts
    kualitas_test = [a.score.quality for a in kasus["TEST-02"].attempts]

    # Act
    sempit = store.get_case_scores(sesi.id, run.grading_run_id)

    # Assert
    assert dev_kosong.is_technical_disruption is True
    assert sempit.scores[kasus["DEV-01"].case_id] == dev_dinilai.score
    assert len(kualitas_test) == 2
    assert sempit.scores[kasus["TEST-02"].case_id].quality == (
        sum(kualitas_test, Decimal(0)) / 2
    ).quantize(Decimal("0.01"))
    assert kasus["KOSONG-03"].case_id not in sempit.scores


def test_run_tidak_dikenal_sesi_ini_tidak_dibaca(db_session, run_dinilai, produk, pembuat):
    """Negatif: id run sesi lain atau id acak dijawab None, bukan skor sesi lain."""
    # Arrange
    sesi, run = run_dinilai
    store = DbGradingStore(db_session)

    # Act
    acak = store.get_case_scores(sesi.id, uuid.uuid4())
    sesi_lain = store.get_case_scores(uuid.uuid4(), run.grading_run_id)

    # Assert
    assert acak is None
    assert sesi_lain is None


# --- DbGradingStore.get_grading_case ---------------------------------------------


def test_satu_kasus_sama_dengan_kasus_di_run_lengkap(db_session, run_dinilai):
    """Positif: soal, jawaban asli, skor, alasan, dan kriteria sama dengan run lengkap."""
    # Arrange
    sesi, run = run_dinilai
    store = DbGradingStore(db_session)
    lengkap = store.get_grading_run(sesi.id, run.grading_run_id)

    for kasus in lengkap.cases:
        # Act
        satu = store.get_grading_case(sesi.id, run.grading_run_id, kasus.case_id)

        # Assert
        assert satu == RunCase(lengkap.rubric_version, kasus), kasus.case_code


def test_kasus_yang_tidak_ada_di_sesi_dijawab_none(db_session, run_dinilai):
    """Negatif: kasus di luar snapshot sesi ini dan run yang tidak dikenal sama-sama None."""
    # Arrange
    sesi, run = run_dinilai
    kasus_id = run.cases[0].case_id
    store = DbGradingStore(db_session)

    # Act
    kasus_asing = store.get_grading_case(sesi.id, run.grading_run_id, uuid.uuid4())
    run_asing = store.get_grading_case(sesi.id, uuid.uuid4(), kasus_id)

    # Assert
    assert kasus_asing is None
    assert run_asing is None


def test_masukan_satu_kasus_hanya_memuat_kasus_dan_percobaannya(db_session, run_dinilai):
    """Positif: load_case_grade_inputs(case_id=...) tidak memuat soal dan jawaban kasus lain."""
    # Arrange
    sesi, run = run_dinilai
    kasus = next(c for c in run.cases if c.case_code == "TEST-02")

    # Act
    masukan = load_case_grade_inputs(db_session, sesi.id, case_id=kasus.case_id)

    # Assert
    assert [m.case_code for m in masukan] == ["TEST-02"]
    assert [a.repetition_no for a in masukan[0].attempts] == [1, 2]


# --- MemoryGradingStore (pengganti di tes service) -------------------------------

RUN = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
SESI = uuid.UUID("00000000-0000-0000-0000-00000000005a")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000d1")


def _store_memori() -> tuple[MemoryGradingStore, CaseGradeResult]:
    skor = ScorePair(deterministic=Decimal("0.80"), quality=Decimal("0.75"))
    kasus = CaseGradeResult(
        snapshot_item_id=uuid.uuid4(), case_id=KASUS, case_code="UAT-01", title="t", scores=skor
    )
    store = MemoryGradingStore()
    store.save_grading_run(
        SessionGradingResponse(
            grading_run_id=RUN,
            session_id=SESI,
            rubric_version="v4",
            kind="initial",
            status="completed",
            triggered_by=uuid.uuid4(),
            triggered_at="2026-11-30T00:00:00Z",
            scores=ThreeScores(dev=ScorePair(), test=ScorePair(), combined=ScorePair()),
            cases=[kasus],
        )
    )
    return store, kasus


def test_store_memori_memberi_skor_dan_kasus_dari_run_yang_diminta():
    """Positif: pengganti memori menjawab seperti DbGradingStore."""
    # Arrange
    store, kasus = _store_memori()

    # Act
    skor = store.get_case_scores(SESI, RUN)
    satu = store.get_grading_case(SESI, RUN, KASUS)

    # Assert
    assert skor == RunCaseScores("v4", {KASUS: kasus.scores})
    assert satu == RunCase("v4", kasus)


def test_store_memori_run_atau_kasus_tidak_dikenal_dijawab_none():
    """Negatif: run lain dan kasus lain sama-sama None."""
    # Arrange
    store, _ = _store_memori()

    # Act / Assert
    assert store.get_case_scores(SESI, uuid.uuid4()) is None
    assert store.get_grading_case(SESI, uuid.uuid4(), KASUS) is None
    assert store.get_grading_case(SESI, RUN, uuid.uuid4()) is None
