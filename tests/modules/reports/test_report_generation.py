"""Pembuat laporan perbandingan. PBI-15 SCRUM-79 SCRUM-313.

AC1, AC2, AC6, AC7, AC9, AC12, AC14. Pembaca sesi diganti pembaca palsu; yang tersimpan
diperiksa di SQLite lewat reports.service.save_report yang asli.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    DUPLICATE_PRODUCT,
    NEED_AT_LEAST_TWO_PRODUCTS,
    NO_SHARED_CASES,
    RUBRIC_VERSION,
    SESSION_CANCELLED,
    SUITE_VERSION,
    CaseRef,
    SessionFacts,
)
from app.modules.reports.creation_policy import REPORT_SCOPE_FORBIDDEN
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    Report,
    ReportCaseBasis,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.report_generation import (
    REPORT_NOT_COMPARABLE,
    REPORT_SESSION_NOT_FOUND,
    ReportGenerator,
    ReportRequest,
)
from app.modules.reports.report_scoring import CallCounts, GradedCase
from app.modules.reports.session_record import SessionRecord
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType, SplitFilter
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.shared.security import Role

RUBRIK = uuid.UUID("11111111-1111-1111-1111-111111111111")
PENILAI = uuid.UUID("22222222-2222-2222-2222-222222222222")
SUITE = uuid.UUID("33333333-3333-3333-3333-333333333333")
PEMBUAT = uuid.UUID("44444444-4444-4444-4444-444444444444")
AWAL = datetime(2026, 11, 1, tzinfo=UTC)


def _ref(nomor: int, *, versi: int = 1, split: SplitTag = SplitTag.TEST) -> CaseRef:
    return CaseRef(
        case_id=uuid.UUID(f"{nomor:032x}"),
        case_version_id=uuid.UUID(f"{nomor:016x}{versi:016x}"),
        case_code=f"CASE-{nomor:03d}",
        split_tag=split.value,
    )


def _nilai(nomor: int, det: str | None, llm: str | None, split: SplitTag = SplitTag.TEST):
    return GradedCase(
        ref=_ref(nomor, split=split),
        split=split,
        det=None if det is None else Decimal(det),
        llm=None if llm is None else Decimal(llm),
    )


def _rekaman(nama: str, graded: list[GradedCase], **ubah) -> SessionRecord:
    tipe = ubah.pop("session_type", SessionType.AUTOMATIC)
    kasus_snapshot = tuple(g.ref for g in graded) + tuple(ubah.pop("snapshot_tambahan", ()))
    facts = SessionFacts(
        session_id=ubah.pop("session_id", uuid.uuid4()),
        product_id=ubah.pop("product_id", uuid.uuid4()),
        product_name=nama,
        session_type=tipe,
        purpose=ubah.pop("purpose", SessionPurpose.OFFICIAL),
        status=ubah.pop("status", SessionStatus.FINISHED),
        accepted_as_is_at=None,
        rubric_version_id=ubah.pop("rubrik", RUBRIK),
        judge_provider_id=PENILAI,
        judge_model_version=ubah.pop("judge_versi", "judge-1"),
        suite_ids=frozenset({SUITE}),
        model_version=ubah.pop("model", "model-1"),
        repetitions=ubah.pop("repetisi", 1),
        answer_date_from=ubah.pop("dari", None),
        answer_date_to=ubah.pop("sampai", None),
        scored_cases=tuple(g.ref for g in graded),
        snapshot_cases=kasus_snapshot,
        split_filter=SplitFilter.BOTH,
    )
    return SessionRecord(
        facts=facts,
        graded_cases=tuple(graded),
        calls_by_split=ubah.pop("calls", {}),
    )


class PembacaPalsu:
    def __init__(self, *rekaman: SessionRecord) -> None:
        self._by_id = {r.facts.session_id: r for r in rekaman}
        self.dipanggil: list[list[uuid.UUID]] = []

    def read(self, session_ids):
        self.dipanggil.append(list(session_ids))
        return [self._by_id[i] for i in session_ids if i in self._by_id]


def _buat(db, *rekaman, role=Role.ADMIN, **ubah):
    permintaan = {
        "title": "Laporan Uji",
        "session_ids": tuple(r.facts.session_id for r in rekaman),
        "score_scope": ScoreScope.TEST,
    } | ubah
    return ReportGenerator(db, PembacaPalsu(*rekaman)).generate(
        actor_id=PEMBUAT, role=role, request=ReportRequest(**permintaan)
    )


def _jumlah(db, model) -> int:
    return db.query(model).count()


@pytest.fixture
def dua_produk():
    """A unggul di dua kasus yang sama-sama dijawab; B menjawab satu kasus tambahan."""
    # Kasus 3 ada di snapshot A tetapi tidak dinilai: split test keduanya identik (AC13).
    a = _rekaman(
        "Produk A",
        [_nilai(1, "0.80", "0.60"), _nilai(2, "0.60", "0.40")],
        snapshot_tambahan=(_ref(3),),
    )
    b = _rekaman(
        "Produk B",
        [_nilai(1, "0.40", "0.40"), _nilai(2, "0.20", "0.20"), _nilai(3, "1.00", "1.00")],
    )
    return a, b


class TestSuccessfulReport:
    def test_saves_one_frozen_report_with_a_row_per_product(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, a, b, title="Perbandingan November")

        # Assert
        assert hasil.warning is None
        assert hasil.report.title == "Perbandingan November"
        assert hasil.report.created_by == PEMBUAT
        assert hasil.report.score_scope is ScoreScope.TEST
        assert hasil.report.has_dev_scores is False
        assert hasil.report.rubric_version_id == RUBRIK
        assert hasil.report.judge_model_name == "judge-1"
        assert _jumlah(db_session, Report) == 1
        assert _jumlah(db_session, ReportSession) == 2
        assert _jumlah(db_session, ReportResult) == 2

    def test_scores_use_only_the_cases_every_product_answered(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, a, b)

        # Assert: kasus 3 milik B saja tidak ikut; B dihitung dari kasus 1 dan 2.
        by_session = {r.session_id: r for r in hasil.results}
        hasil_b = by_session[b.facts.session_id]
        assert hasil_b.det_score == Decimal("0.30")
        assert hasil_b.llm_score == Decimal("0.30")
        assert hasil_b.cases_graded_count == 3
        assert hasil_b.cases_basis_count == 2
        assert hasil.report.case_basis_count == 2

    def test_scores_are_two_decimals_and_kept_apart(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        hasil_a = {r.session_id: r for r in hasil.results}[a.facts.session_id]
        assert hasil_a.det_score == Decimal("0.70")
        assert hasil_a.llm_score == Decimal("0.50")
        assert hasil_a.total_score == Decimal("0.60")

    def test_ranks_products_by_total_score(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        peringkat = {r.session_id: r.rank for r in hasil.results}
        assert peringkat == {a.facts.session_id: 1, b.facts.session_id: 2}

    def test_stores_the_case_basis_as_reference_id_and_version(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        _buat(db_session, a, b)

        # Assert
        dasar = {
            (d.case_id, d.case_version_id, d.split_tag) for d in db_session.query(ReportCaseBasis)
        }
        assert dasar == {
            (_ref(1).case_id, _ref(1).case_version_id, SplitTag.TEST),
            (_ref(2).case_id, _ref(2).case_version_id, SplitTag.TEST),
        }

    def test_a_product_without_a_total_score_is_not_ranked(self, db_session):
        # Arrange
        tanpa_llm = _rekaman("Tanpa LLM", [_nilai(1, "0.90", None)])
        lengkap = _rekaman("Lengkap", [_nilai(1, "0.10", "0.10")])

        # Act
        hasil = _buat(db_session, tanpa_llm, lengkap)

        # Assert
        by_session = {r.session_id: r for r in hasil.results}
        assert by_session[tanpa_llm.facts.session_id].total_score is None
        assert by_session[tanpa_llm.facts.session_id].rank is None
        assert by_session[lengkap.facts.session_id].rank == 1


class TestFailureRate:
    def test_is_failed_over_all_calls_for_automatic_and_empty_for_manual(self, db_session):
        # Arrange
        otomatis = _rekaman(
            "Otomatis",
            [_nilai(1, "0.5", "0.5")],
            calls={SplitTag.TEST: CallCounts(total=4, failed=1)},
        )
        manual = _rekaman("Manual", [_nilai(1, "0.5", "0.5")], session_type=SessionType.MANUAL)

        # Act
        hasil = _buat(db_session, otomatis, manual)

        # Assert
        by_session = {r.session_id: r for r in hasil.results}
        assert by_session[otomatis.facts.session_id].failure_rate == Decimal("25.00")
        assert by_session[manual.facts.session_id].failure_rate is None

    def test_a_test_report_ignores_the_calls_of_dev_cases(self, db_session):
        # Arrange
        a = _rekaman(
            "A",
            [_nilai(1, "0.5", "0.5")],
            calls={SplitTag.TEST: CallCounts(4, 1), SplitTag.DEV: CallCounts(10, 10)},
        )
        b = _rekaman("B", [_nilai(1, "0.5", "0.5")])

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        assert {r.session_id: r for r in hasil.results}[a.facts.session_id].failure_rate == Decimal(
            "25.00"
        )


class TestDevScores:
    @pytest.fixture
    def campuran(self):
        def isi(det_test: str, det_dev: str):
            return [
                _nilai(1, det_test, det_test),
                _nilai(2, det_test, det_test),
                _nilai(3, det_dev, det_dev, SplitTag.DEV),
                _nilai(4, det_dev, det_dev, SplitTag.DEV),
            ]

        return _rekaman("A", isi("0.80", "0.20")), _rekaman("B", isi("0.40", "0.60"))

    def test_a_test_report_never_counts_dev_cases(self, db_session, campuran):
        # Arrange
        a, b = campuran

        # Act
        hasil = _buat(db_session, a, b, score_scope=ScoreScope.TEST)

        # Assert
        assert {r.score_split for r in hasil.results} == {ScoreSplit.TEST}
        assert hasil.report.has_dev_scores is False
        assert hasil.report.case_basis_count == 2
        assert {d.split_tag for d in db_session.query(ReportCaseBasis)} == {SplitTag.TEST}

    def test_a_dev_report_counts_only_dev_cases(self, db_session, campuran):
        # Arrange
        a, b = campuran

        # Act
        hasil = _buat(db_session, a, b, score_scope=ScoreScope.DEV)

        # Assert
        assert {r.score_split for r in hasil.results} == {ScoreSplit.DEV}
        assert hasil.report.has_dev_scores is True
        by_session = {r.session_id: r for r in hasil.results}
        assert by_session[a.facts.session_id].det_score == Decimal("0.20")
        assert by_session[b.facts.session_id].rank == 1

    def test_a_dev_plus_test_report_has_test_dev_and_combined_rows_per_product(
        self, db_session, campuran
    ):
        # Arrange
        a, b = campuran

        # Act
        hasil = _buat(db_session, a, b, score_scope=ScoreScope.DEV_TEST)

        # Assert
        assert hasil.report.has_dev_scores is True
        assert hasil.report.case_basis_count == 4
        baris_a = {r.score_split: r for r in hasil.results if r.session_id == a.facts.session_id}
        assert set(baris_a) == {ScoreSplit.TEST, ScoreSplit.DEV, ScoreSplit.COMBINED}
        assert baris_a[ScoreSplit.TEST].det_score == Decimal("0.80")
        assert baris_a[ScoreSplit.DEV].det_score == Decimal("0.20")
        assert baris_a[ScoreSplit.COMBINED].det_score == Decimal("0.50")

    def test_each_score_split_is_ranked_on_its_own(self, db_session, campuran):
        # Arrange
        a, b = campuran

        # Act
        hasil = _buat(db_session, a, b, score_scope=ScoreScope.DEV_TEST)

        # Assert: A menang di Test, B menang di Dev, gabungan seri sehingga diputus label.
        peringkat = {(r.session_id, r.score_split): r.rank for r in hasil.results}
        assert peringkat[(a.facts.session_id, ScoreSplit.TEST)] == 1
        assert peringkat[(b.facts.session_id, ScoreSplit.DEV)] == 1
        assert peringkat[(a.facts.session_id, ScoreSplit.COMBINED)] == 1
        assert peringkat[(b.facts.session_id, ScoreSplit.COMBINED)] == 2

    def test_failure_rate_is_counted_per_score_split(self, db_session):
        # Arrange
        a = _rekaman(
            "A",
            [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5", SplitTag.DEV)],
            calls={SplitTag.TEST: CallCounts(4, 1), SplitTag.DEV: CallCounts(1, 1)},
        )
        b = _rekaman("B", [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5", SplitTag.DEV)])

        # Act
        hasil = _buat(db_session, a, b, score_scope=ScoreScope.DEV_TEST)

        # Assert
        laju = {
            r.score_split: r.failure_rate
            for r in hasil.results
            if r.session_id == a.facts.session_id
        }
        assert laju == {
            ScoreSplit.TEST: Decimal("25.00"),
            ScoreSplit.DEV: Decimal("100.00"),
            ScoreSplit.COMBINED: Decimal("40.00"),
        }


class TestAccess:
    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_viewer_and_contributor_cannot_create_dev_reports(
        self, db_session, dua_produk, role, scope
    ):
        # Arrange
        a, b = dua_produk
        pembaca = PembacaPalsu(a, b)
        pembuat = ReportGenerator(db_session, pembaca)

        # Act
        with pytest.raises(ForbiddenError) as galat:
            pembuat.generate(
                actor_id=PEMBUAT,
                role=role,
                request=ReportRequest(
                    title="Dev",
                    session_ids=(a.facts.session_id, b.facts.session_id),
                    score_scope=scope,
                ),
            )

        # Assert: ditolak sebelum menyentuh data apa pun.
        assert galat.value.code == REPORT_SCOPE_FORBIDDEN
        assert pembaca.dipanggil == []
        assert _jumlah(db_session, Report) == 0

    def test_viewer_may_create_a_test_report(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, a, b, role=Role.VIEWER)

        # Assert
        assert hasil.report is not None

    def test_developer_ai_may_create_a_dev_report(self, db_session):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5", SplitTag.DEV)])
        b = _rekaman("B", [_nilai(1, "0.5", "0.5", SplitTag.DEV)])

        # Act
        hasil = _buat(db_session, a, b, role=Role.DEVELOPER_AI, score_scope=ScoreScope.DEV)

        # Assert
        assert hasil.report.has_dev_scores is True


class TestLabels:
    def test_uses_the_real_product_name_by_default(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        _buat(db_session, a, b)

        # Assert
        labels = {s.product_label for s in db_session.query(ReportSession)}
        assert labels == {"Produk A", "Produk B"}

    def test_admin_can_hide_names_behind_codes_in_the_order_requested(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act
        hasil = _buat(db_session, b, a, product_label_mode=ProductLabelMode.CODE)

        # Assert
        label = {s.session_id: s.product_label for s in hasil.sessions}
        assert label == {b.facts.session_id: "Product A", a.facts.session_id: "Product B"}

    def test_only_admin_may_use_codes(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk

        # Act and Assert
        with pytest.raises(ForbiddenError):
            _buat(db_session, a, b, role=Role.VIEWER, product_label_mode=ProductLabelMode.CODE)


class TestSessionLookup:
    def test_unknown_session_is_not_found(self, db_session, dua_produk):
        # Arrange
        a, _ = dua_produk
        hilang = uuid.uuid4()
        pembuat = ReportGenerator(db_session, PembacaPalsu(a))

        # Act
        with pytest.raises(NotFoundError) as galat:
            pembuat.generate(
                actor_id=PEMBUAT,
                role=Role.ADMIN,
                request=ReportRequest(
                    title="X",
                    session_ids=(a.facts.session_id, hilang),
                    score_scope=ScoreScope.TEST,
                ),
            )

        # Assert
        assert galat.value.status_code == 404
        assert galat.value.code == REPORT_SESSION_NOT_FOUND
        assert galat.value.details == {"missing": [str(hilang)]}
        assert _jumlah(db_session, Report) == 0


class TestNotComparable:
    def test_an_ineligible_session_is_refused_with_its_reason(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, a, batal)

        # Assert
        assert galat.value.status_code == 409
        assert galat.value.code == REPORT_NOT_COMPARABLE
        assert galat.value.details["ineligible"] == {
            str(batal.facts.session_id): [SESSION_CANCELLED]
        }
        assert _jumlah(db_session, Report) == 0

    def test_a_failed_comparison_requirement_is_explained(self, db_session, dua_produk):
        # Arrange
        a, _ = dua_produk
        lain_rubrik = _rekaman(
            "Lain",
            [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5")],
            snapshot_tambahan=(_ref(3),),
            rubrik=uuid.UUID(int=99),
        )

        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, a, lain_rubrik)

        # Assert
        syarat = [t["requirement"] for t in galat.value.details["rejections"]]
        assert syarat == [RUBRIC_VERSION]
        assert all(t["message"] for t in galat.value.details["rejections"])

    def test_one_product_alone_is_not_a_comparison(self, db_session, dua_produk):
        # Arrange
        a, _ = dua_produk

        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, a)

        # Assert
        assert galat.value.details["selection_errors"] == [NEED_AT_LEAST_TWO_PRODUCTS]

    def test_the_same_session_twice_is_refused(self, db_session, dua_produk):
        # Arrange
        a, b = dua_produk
        pembuat = ReportGenerator(db_session, PembacaPalsu(a, b))

        # Act
        with pytest.raises(ConflictError) as galat:
            pembuat.generate(
                actor_id=PEMBUAT,
                role=Role.ADMIN,
                request=ReportRequest(
                    title="Ganda",
                    session_ids=(a.facts.session_id, a.facts.session_id, b.facts.session_id),
                    score_scope=ScoreScope.TEST,
                ),
            )

        # Assert
        assert DUPLICATE_PRODUCT in galat.value.details["selection_errors"]
        assert _jumlah(db_session, Report) == 0


def _beda_versi_kasus_1():
    """Dua sesi yang menilai CASE-001 pada versi berbeda, jadi syarat suite gagal."""
    a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
    versi_dua = GradedCase(
        ref=_ref(1, versi=2), split=SplitTag.TEST, det=Decimal("0.5"), llm=Decimal("0.5")
    )
    return a, _rekaman("B", [versi_dua])


class TestRejectionCaseCodes:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.CONTRIBUTOR])
    def test_roles_that_may_read_cases_see_the_conflicting_case_codes(self, db_session, role):
        # Arrange
        a, b = _beda_versi_kasus_1()

        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, a, b, role=role)

        # Assert
        (tolakan,) = galat.value.details["rejections"]
        assert tolakan["requirement"] == SUITE_VERSION
        assert "CASE-001" in tolakan["message"]

    @pytest.mark.parametrize("role", [Role.DEVELOPER_AI, Role.VIEWER])
    def test_roles_that_may_not_read_cases_get_a_message_without_case_codes(self, db_session, role):
        # Arrange
        a, b = _beda_versi_kasus_1()

        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, a, b, role=role)

        # Assert
        (tolakan,) = galat.value.details["rejections"]
        assert tolakan["requirement"] == SUITE_VERSION
        assert tolakan["message"]
        assert "CASE-" not in tolakan["message"]
        assert _jumlah(db_session, Report) == 0


class TestNoSharedCases:
    def test_returns_a_warning_and_saves_nothing(self, db_session):
        # Arrange
        a = _rekaman(
            "A",
            [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5")],
            snapshot_tambahan=(_ref(3), _ref(4)),
        )
        b = _rekaman(
            "B",
            [_nilai(3, "0.5", "0.5"), _nilai(4, "0.5", "0.5")],
            snapshot_tambahan=(_ref(1), _ref(2)),
        )

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        assert hasil.report is None
        assert hasil.warning == NO_SHARED_CASES
        assert hasil.results == ()
        assert _jumlah(db_session, Report) == 0


class TestMeasurementFilter:
    @pytest.fixture
    def tiga(self):
        return (
            _rekaman("Otomatis 1", [_nilai(1, "0.5", "0.5")]),
            _rekaman("Otomatis 2", [_nilai(1, "0.6", "0.6")]),
            _rekaman("Manual", [_nilai(1, "0.7", "0.7")], session_type=SessionType.MANUAL),
        )

    def test_automatic_filter_leaves_manual_products_out(self, db_session, tiga):
        # Act
        hasil = _buat(db_session, *tiga, measurement_filter=MeasurementFilter.AUTOMATIC)

        # Assert
        assert {s.measurement_kind for s in hasil.sessions} == {MeasurementKind.AUTOMATIC}
        assert len(hasil.sessions) == 2
        assert hasil.report.measurement_filter is MeasurementFilter.AUTOMATIC

    def test_all_keeps_automatic_and_manual_together_with_their_labels(self, db_session, tiga):
        # Act
        hasil = _buat(db_session, *tiga, measurement_filter=MeasurementFilter.ALL)

        # Assert
        assert sorted(s.measurement_kind.value for s in hasil.sessions) == [
            "automatic",
            "automatic",
            "manual",
        ]

    def test_a_filter_that_leaves_fewer_than_two_products_is_refused(self, db_session, tiga):
        # Act
        with pytest.raises(ConflictError) as galat:
            _buat(db_session, *tiga, measurement_filter=MeasurementFilter.MANUAL)

        # Assert
        assert galat.value.details["selection_errors"] == [NEED_AT_LEAST_TWO_PRODUCTS]


class TestMarkers:
    @staticmethod
    def _kode(sesi: ReportSession) -> set[str]:
        return {penanda["code"] for penanda in sesi.markers}

    def test_a_trial_session_is_flagged_on_the_session_and_not_in_markers(self, db_session):
        # Arrange
        uji = _rekaman("Uji", [_nilai(1, "0.5", "0.5")], purpose=SessionPurpose.TRIAL)
        resmi = _rekaman("Resmi", [_nilai(1, "0.5", "0.5")])

        # Act
        hasil = _buat(db_session, uji, resmi)

        # Assert
        by_session = {s.session_id: s for s in hasil.sessions}
        assert by_session[uji.facts.session_id].is_trial is True
        assert by_session[resmi.facts.session_id].is_trial is False
        assert by_session[uji.facts.session_id].markers == []

    def test_a_manual_answer_range_over_seven_days_is_flagged(self, db_session):
        # Arrange
        lama = _rekaman(
            "Manual",
            [_nilai(1, "0.5", "0.5")],
            session_type=SessionType.MANUAL,
            dari=AWAL,
            sampai=AWAL + timedelta(days=10),
        )
        otomatis = _rekaman("Otomatis", [_nilai(1, "0.5", "0.5")])

        # Act
        hasil = _buat(db_session, lama, otomatis)

        # Assert
        by_session = {s.session_id: s for s in hasil.sessions}
        assert self._kode(by_session[lama.facts.session_id]) == {"manual_date_span_over_7_days"}
        assert by_session[otomatis.facts.session_id].markers == []

    def test_different_model_versions_and_repetitions_are_flagged_on_every_product(
        self, db_session
    ):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], model="model-1", repetisi=1)
        b = _rekaman("B", [_nilai(1, "0.5", "0.5")], model="model-2", repetisi=3)

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        for sesi in hasil.sessions:
            assert self._kode(sesi) == {"model_version_differs", "repetitions_differ"}
            assert all(set(penanda) == {"code", "detail"} for penanda in sesi.markers)

    def test_results_keep_the_model_versions_and_repetitions_that_were_used(self, db_session):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], model="model-1", repetisi=2)
        b = _rekaman("B", [_nilai(1, "0.5", "0.5")], model="model-2", repetisi=2)

        # Act
        hasil = _buat(db_session, a, b)

        # Assert
        by_session = {r.session_id: r for r in hasil.results}
        assert by_session[a.facts.session_id].product_model_version == "model-1"
        assert by_session[a.facts.session_id].repetitions == 2
        assert by_session[a.facts.session_id].judge_model_version == "judge-1"
