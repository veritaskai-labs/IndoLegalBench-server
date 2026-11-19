"""Pembaca data sesi untuk pembuat laporan, di SQLite. PBI-15 SCRUM-79 SCRUM-313.

Data sintetis (L3). SQLite tidak menegakkan foreign key, jadi suite dan versi kasus cukup UUID
acak: yang diuji adalah cara data dibaca dan dirakit, bukan skemanya.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.auth.models import User
from app.modules.cases.models import SplitTag
from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    JudgeKind,
    JudgeStatus,
    LlmJudgeResult,
    RubricVersion,
)
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.reports.report_scoring import CallCounts
from app.modules.reports.session_reader import SqlSessionRecordReader
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    MeasurementSessionSuite,
    SessionProductCopy,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role

SEKARANG = datetime(2026, 11, 10, 12, tzinfo=UTC)


@pytest.fixture
def admin(db_session):
    pengguna = User(email="reader-313@test.local", name="Admin", role=Role.ADMIN)
    db_session.add(pengguna)
    db_session.flush()
    return pengguna


@pytest.fixture
def rubrik(db_session, admin):
    baris = RubricVersion(
        version_no=1,
        effective_date=date(2026, 11, 1),
        judge_prompt_quality="Prompt kualitas sintetis.",
        judge_prompt_format="Prompt format sintetis.",
        created_by=admin.id,
    )
    db_session.add(baris)
    db_session.flush()
    return baris


def _sesi(db, admin, *, nama="Produk A", salinan=True, **ubah) -> MeasurementSession:
    produk = AiProduct(
        name=f"{nama} (master)",
        created_by=admin.id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Penyedia Uji",
        site_url="https://contoh.test",
    )
    db.add(produk)
    db.flush()
    isi = {
        "label": f"Sesi {nama}",
        "purpose": SessionPurpose.OFFICIAL,
        "session_type": SessionType.AUTOMATIC,
        "product_id": produk.id,
        "repetitions": 2,
        "split_filter": SplitFilter.BOTH,
        "status": SessionStatus.FINISHED,
        "created_by": admin.id,
    } | ubah
    sesi = MeasurementSession(**isi)
    db.add(sesi)
    db.flush()
    if salinan:
        db.add(
            SessionProductCopy(
                session_id=sesi.id,
                product_id=produk.id,
                name=nama,
                measurement_type="automatic",
                model_version_at_start="model-2026-10",
            )
        )
    db.flush()
    return sesi


def _kasus(db, sesi, kode, split="test", *, versi=None) -> CaseSnapshotItem:
    snapshot = db.query(CaseSnapshot).filter_by(session_id=sesi.id).one_or_none()
    if snapshot is None:
        snapshot = CaseSnapshot(session_id=sesi.id)
        db.add(snapshot)
        db.flush()
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid5(uuid.NAMESPACE_OID, kode),
        case_version_id=versi or uuid.uuid5(uuid.NAMESPACE_OID, f"{kode}-v1"),
        body={"sections": {"case_code": kode, "split_tag": split, "category": None}},
    )
    db.add(item)
    db.flush()
    return item


def _percobaan(db, sesi, item, nomor=1) -> Attempt:
    baris = Attempt(
        session_id=sesi.id,
        snapshot_item_id=item.id,
        repetition_no=nomor,
        source=AttemptSource.AUTOMATIC,
    )
    db.add(baris)
    db.flush()
    return baris


def _panggilan(db, sesi, percobaan, *kegagalan) -> None:
    """Satu baris call_logs per elemen; None berarti upaya itu berhasil."""
    for nomor, jenis in enumerate(kegagalan, start=1):
        db.add(
            CallLog(
                attempt_id=percobaan.id,
                product_id=sesi.product_id,
                started_at=SEKARANG,
                ended_at=SEKARANG,
                duration_ms=10,
                usd_idr_rate=Decimal("16000"),
                model_version="model-2026-10",
                result=jenis or "ok",
                failure_kind=jenis,
                raw={},
                try_no=nomor,
            )
        )
    db.flush()


def _nilai(
    db,
    sesi,
    rubrik,
    admin,
    skor,
    *,
    penilai=None,
    versi_judge=("judge-1",),
    selesai=True,
    terbaru=True,
) -> GradingRun:
    """skor: daftar (percobaan, det, kualitas); None pada det berarti gangguan teknis."""
    run = GradingRun(
        session_id=sesi.id,
        rubric_version_id=rubrik.id,
        judge_provider_id=penilai,
        kind=GradingRunKind.INITIAL,
        status=GradingRunStatus.FINISHED if selesai else GradingRunStatus.RUNNING,
        finished_at=SEKARANG if selesai else None,
        is_latest=terbaru,
        triggered_by=admin.id,
    )
    db.add(run)
    db.flush()
    for urutan, (percobaan, det, kualitas) in enumerate(skor):
        hasil = GradingResult(
            grading_run_id=run.id,
            attempt_id=percobaan.id,
            is_technical_disruption=det is None,
            det_score=None if det is None else Decimal(det),
        )
        if det is not None:
            hasil.det_regulation_matches = []
            hasil.det_article_matches = []
            hasil.det_ungradable_refs = []
            hasil.det_all_chained_cited = True
            hasil.det_reference_total = 4
            hasil.det_reference_correct = 3
            hasil.det_extracted_total = 4
            hasil.det_extracted_correct = 3
            hasil.det_formula = "recall"
        db.add(hasil)
        db.flush()
        if kualitas is not None:
            db.add(
                LlmJudgeResult(
                    grading_result_id=hasil.id,
                    judge_run_no=1,
                    kind=JudgeKind.QUALITY,
                    status=JudgeStatus.SUCCEEDED,
                    prompt="Prompt sintetis",
                    criteria_met=1,
                    criteria_total=4,
                    criteria_score=Decimal(kualitas),
                    model_version=versi_judge[urutan % len(versi_judge)],
                )
            )
    db.flush()
    return run


def _baca(db, *ids):
    return SqlSessionRecordReader(db).read(list(ids))


class TestSessionFacts:
    def test_reads_what_the_comparison_rules_need_from_the_session(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(
            db_session,
            admin,
            session_type=SessionType.MANUAL,
            purpose=SessionPurpose.TRIAL,
            answer_date_from=SEKARANG,
            answer_date_to=SEKARANG,
        )
        suite = uuid.uuid4()
        db_session.add(MeasurementSessionSuite(session_id=sesi.id, suite_id=suite))
        db_session.flush()

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        facts = rekaman.facts
        assert facts.session_id == sesi.id
        assert facts.product_id == sesi.product_id
        assert facts.session_type is SessionType.MANUAL
        assert facts.purpose is SessionPurpose.TRIAL
        assert facts.status is SessionStatus.FINISHED
        assert facts.repetitions == 2
        assert facts.suite_ids == frozenset({suite})
        assert facts.model_version == "model-2026-10"
        # SQLite mengembalikan waktu tanpa zona; yang dibandingkan jamnya.
        assert facts.answer_date_from.replace(tzinfo=None) == SEKARANG.replace(tzinfo=None)
        assert facts.answer_date_to.replace(tzinfo=None) == SEKARANG.replace(tzinfo=None)

    def test_product_name_comes_from_the_copy_taken_when_the_session_started(
        self, db_session, admin
    ):
        # Arrange
        sesi = _sesi(db_session, admin, nama="Nama Asli")

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.product_name == "Nama Asli"

    def test_falls_back_to_the_product_name_when_there_is_no_copy(self, db_session, admin):
        # Arrange
        sesi = _sesi(db_session, admin, nama="Tanpa Salinan", salinan=False)

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.product_name == "Tanpa Salinan (master)"
        assert rekaman.facts.model_version is None

    @pytest.mark.parametrize("filter_sesi", [SplitFilter.TEST, SplitFilter.DEV, SplitFilter.BOTH])
    def test_reads_the_split_filter_the_snapshot_was_made_with(
        self, db_session, admin, filter_sesi
    ):
        # Arrange
        sesi = _sesi(db_session, admin, split_filter=filter_sesi)

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.split_filter is filter_sesi

    def test_every_case_carries_the_split_tag_of_its_snapshot_item(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(db_session, admin)
        dinilai = _kasus(db_session, sesi, "CASE-001", "test")
        _kasus(db_session, sesi, "CASE-002", "dev")
        _nilai(
            db_session, sesi, rubrik, admin, [(_percobaan(db_session, sesi, dinilai), "0.5", "0.5")]
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert {k.case_code: k.split_tag for k in rekaman.facts.snapshot_cases} == {
            "CASE-001": "test",
            "CASE-002": "dev",
        }
        assert [k.split_tag for k in rekaman.facts.scored_cases] == ["test"]

    def test_snapshot_cases_list_every_case_even_those_without_a_score(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        dinilai = _kasus(db_session, sesi, "CASE-001", "test")
        _kasus(db_session, sesi, "CASE-002", "dev")
        _nilai(
            db_session, sesi, rubrik, admin, [(_percobaan(db_session, sesi, dinilai), "0.5", "0.5")]
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert sorted(k.case_code for k in rekaman.facts.snapshot_cases) == ["CASE-001", "CASE-002"]
        assert [k.case_code for k in rekaman.facts.scored_cases] == ["CASE-001"]


class TestGradingIdentity:
    def test_rubric_and_judge_come_from_the_latest_finished_grading_run(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        penilai = uuid.uuid4()
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [(_percobaan(db_session, sesi, item), "0.5", "0.5")],
            penilai=penilai,
            versi_judge=("judge-2026-10",),
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.rubric_version_id == rubrik.id
        assert rekaman.facts.judge_provider_id == penilai
        assert rekaman.facts.judge_model_version == "judge-2026-10"

    def test_a_session_without_a_finished_grading_run_counts_as_not_graded(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [(_percobaan(db_session, sesi, item), "0.5", "0.5")],
            selesai=False,
            terbaru=False,
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.rubric_version_id is None
        assert rekaman.graded_cases == ()

    def test_judge_model_is_unknown_when_the_run_used_different_versions(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        satu = _kasus(db_session, sesi, "CASE-001")
        dua = _kasus(db_session, sesi, "CASE-002")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [
                (_percobaan(db_session, sesi, satu), "0.5", "0.5"),
                (_percobaan(db_session, sesi, dua), "0.5", "0.5"),
            ],
            versi_judge=("judge-a", "judge-b"),
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.judge_model_version is None

    def test_judge_model_is_unknown_when_no_version_was_recorded(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [(_percobaan(db_session, sesi, item), "0.5", "0.5")],
            versi_judge=(None,),
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.facts.judge_model_version is None


class TestGradedCases:
    def test_a_case_score_is_the_mean_of_its_repetitions_at_full_precision(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [
                (_percobaan(db_session, sesi, item, 1), "0.50", "0.25"),
                (_percobaan(db_session, sesi, item, 2), "1.00", "0.50"),
            ],
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        (kasus,) = rekaman.graded_cases
        assert kasus.ref.case_code == "CASE-001"
        assert kasus.split is SplitTag.TEST
        assert kasus.det == Decimal("0.75")
        assert kasus.llm == Decimal("0.375")

    def test_a_technical_disruption_is_skipped_not_counted_as_zero(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        _nilai(
            db_session,
            sesi,
            rubrik,
            admin,
            [
                (_percobaan(db_session, sesi, item, 1), "0.80", "0.60"),
                (_percobaan(db_session, sesi, item, 2), None, None),
            ],
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        (kasus,) = rekaman.graded_cases
        assert kasus.det == Decimal("0.80")
        assert kasus.llm == Decimal("0.60")

    def test_a_case_with_no_score_at_all_is_not_a_graded_case(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(db_session, admin)
        item = _kasus(db_session, sesi, "CASE-001")
        _nilai(db_session, sesi, rubrik, admin, [(_percobaan(db_session, sesi, item), None, None)])

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.graded_cases == ()

    def test_reads_the_split_and_version_of_each_case_from_the_snapshot(
        self, db_session, admin, rubrik
    ):
        # Arrange
        sesi = _sesi(db_session, admin)
        versi = uuid.uuid4()
        item = _kasus(db_session, sesi, "CASE-007", "dev", versi=versi)
        _nilai(
            db_session, sesi, rubrik, admin, [(_percobaan(db_session, sesi, item), "0.5", "0.5")]
        )

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        (kasus,) = rekaman.graded_cases
        assert kasus.split is SplitTag.DEV
        assert kasus.ref.case_version_id == versi
        assert kasus.ref.case_id == item.case_id


class TestCallCounts:
    def test_counts_every_call_including_retries_per_case_split(self, db_session, admin, rubrik):
        # Arrange
        sesi = _sesi(db_session, admin)
        test = _kasus(db_session, sesi, "CASE-001", "test")
        dev = _kasus(db_session, sesi, "CASE-002", "dev")
        _panggilan(db_session, sesi, _percobaan(db_session, sesi, test), "timeout", None)
        _panggilan(db_session, sesi, _percobaan(db_session, sesi, dev), "rate_limited")

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.calls_by_split[SplitTag.TEST] == CallCounts(total=2, failed=1)
        assert rekaman.calls_by_split[SplitTag.DEV] == CallCounts(total=1, failed=1)

    def test_a_session_without_calls_has_no_counts(self, db_session, admin):
        # Arrange
        sesi = _sesi(db_session, admin, session_type=SessionType.MANUAL)
        _percobaan(db_session, sesi, _kasus(db_session, sesi, "CASE-001"))

        # Act
        (rekaman,) = _baca(db_session, sesi.id)

        # Assert
        assert rekaman.calls_by_split == {}


class TestSelection:
    def test_reading_no_ids_gives_no_records_without_touching_the_database(self, db_session):
        # Act
        hasil = _baca(db_session)

        # Assert
        assert hasil == []

    def test_sessions_that_do_not_exist_are_left_out(self, db_session, admin):
        # Arrange
        ada = _sesi(db_session, admin)

        # Act
        hasil = _baca(db_session, ada.id, uuid.uuid4())

        # Assert
        assert [r.facts.session_id for r in hasil] == [ada.id]

    def test_keeps_the_requested_order_and_repeated_ids(self, db_session, admin):
        # Arrange
        a = _sesi(db_session, admin, nama="A")
        b = _sesi(db_session, admin, nama="B")

        # Act
        hasil = _baca(db_session, b.id, a.id, b.id)

        # Assert
        assert [r.facts.session_id for r in hasil] == [b.id, a.id, b.id]

    def test_never_mixes_up_the_data_of_two_sessions(self, db_session, admin, rubrik):
        # Arrange
        a = _sesi(db_session, admin, nama="A")
        b = _sesi(db_session, admin, nama="B")
        item_a = _kasus(db_session, a, "CASE-A")
        item_b = _kasus(db_session, b, "CASE-B")
        _nilai(db_session, a, rubrik, admin, [(_percobaan(db_session, a, item_a), "0.10", "0.10")])
        _nilai(db_session, b, rubrik, admin, [(_percobaan(db_session, b, item_b), "0.90", "0.90")])

        # Act
        rekaman_a, rekaman_b = _baca(db_session, a.id, b.id)

        # Assert
        assert [k.ref.case_code for k in rekaman_a.graded_cases] == ["CASE-A"]
        assert rekaman_a.graded_cases[0].det == Decimal("0.10")
        assert [k.ref.case_code for k in rekaman_b.graded_cases] == ["CASE-B"]
        assert rekaman_b.graded_cases[0].det == Decimal("0.90")
