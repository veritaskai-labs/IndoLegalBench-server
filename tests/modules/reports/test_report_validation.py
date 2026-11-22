"""Validasi langsung (dry-run) pilihan sesi. PBI-15 SCRUM-79 SCRUM-362.

AC1, AC4, AC5, AC13, AC14. Pembaca sesi diganti pembaca palsu, tanpa database: validator
hanya membaca dan tidak menyimpan apa pun.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    DUPLICATE_PRODUCT,
    JUDGE_MODEL,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NEED_AT_LEAST_TWO_PRODUCTS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    RUBRIC_VERSION,
    SESSION_CANCELLED,
    SESSION_NOT_GRADED,
    SESSION_RUN_FAILED,
    SUITE_VERSION,
    TRIAL_SESSION,
)
from app.modules.reports.creation_policy import REPORT_SCOPE_FORBIDDEN
from app.modules.reports.models import MeasurementFilter, ScoreScope
from app.modules.reports.report_planning import REPORT_SESSION_NOT_FOUND
from app.modules.reports.report_scoring import GradedCase
from app.modules.reports.report_validation import ReportValidator, ViolationKind
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.security import Role
from tests.modules.reports.test_report_generation import (
    AWAL,
    PembacaPalsu,
    _nilai,
    _ref,
    _rekaman,
)


def _validasi(*rekaman, role=Role.ADMIN, scope=ScoreScope.TEST, ids=None, **ubah):
    pembaca = PembacaPalsu(*rekaman)
    hasil = ReportValidator(pembaca).validate(
        role=role,
        session_ids=tuple(ids if ids is not None else (r.facts.session_id for r in rekaman)),
        score_scope=scope,
        measurement_filter=ubah.pop("measurement_filter", MeasurementFilter.ALL),
    )
    return hasil, pembaca


def _pelanggaran(hasil) -> set[tuple[ViolationKind, str]]:
    return {(v.kind, v.code) for v in hasil.violations}


def _peringatan(hasil) -> set[tuple[str, uuid.UUID | None]]:
    return {(w.code, w.session_id) for w in hasil.warnings}


@pytest.fixture
def dua_produk():
    a = _rekaman("Produk A", [_nilai(1, "0.8", "0.6"), _nilai(2, "0.6", "0.4")])
    b = _rekaman("Produk B", [_nilai(1, "0.4", "0.4"), _nilai(2, "0.2", "0.2")])
    return a, b


@pytest.fixture
def campuran():
    """Dua produk yang sama-sama menjawab kasus test (1, 2) dan kasus dev (3)."""

    def isi():
        return [
            _nilai(1, "0.5", "0.5"),
            _nilai(2, "0.5", "0.5"),
            _nilai(3, "0.5", "0.5", SplitTag.DEV),
        ]

    return _rekaman("A", isi()), _rekaman("B", isi())


class TestValidSelection:
    def test_a_clean_selection_is_valid_with_nothing_to_report(self, dua_produk):
        # Act
        hasil, _ = _validasi(*dua_produk)

        # Assert
        assert hasil.valid is True
        assert hasil.violations == ()
        assert hasil.warnings == ()
        assert hasil.available_scopes == (ScoreScope.TEST,)

    def test_an_empty_selection_asks_for_two_products_instead_of_failing(self):
        # Act
        hasil, _ = _validasi()

        # Assert
        assert hasil.valid is False
        assert _pelanggaran(hasil) == {(ViolationKind.SELECTION, NEED_AT_LEAST_TWO_PRODUCTS)}
        assert hasil.available_scopes == ()

    def test_one_product_alone_is_not_a_comparison(self, dua_produk):
        # Act
        hasil, _ = _validasi(dua_produk[0])

        # Assert
        assert hasil.valid is False
        assert (ViolationKind.SELECTION, NEED_AT_LEAST_TWO_PRODUCTS) in _pelanggaran(hasil)

    def test_the_same_session_twice_is_reported_not_raised(self, dua_produk):
        # Arrange
        a, b = dua_produk
        ids = (a.facts.session_id, a.facts.session_id, b.facts.session_id)

        # Act
        hasil, _ = _validasi(a, b, ids=ids)

        # Assert
        assert (ViolationKind.SELECTION, DUPLICATE_PRODUCT) in _pelanggaran(hasil)
        assert hasil.valid is False

    def test_every_violation_comes_with_a_readable_message(self, dua_produk):
        # Arrange
        a, _ = dua_produk
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

        # Act
        hasil, _ = _validasi(a, batal)

        # Assert
        assert hasil.violations
        assert all(v.message for v in hasil.violations)


class TestAllViolationsAtOnce:
    def test_selection_session_and_requirement_problems_are_listed_together(self, dua_produk):
        # Arrange
        a, _ = dua_produk
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)
        lain = _rekaman(
            "Lain",
            [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5")],
            rubrik=uuid.UUID(int=99),
        )
        ids = (
            a.facts.session_id,
            a.facts.session_id,
            batal.facts.session_id,
            lain.facts.session_id,
        )

        # Act
        hasil, _ = _validasi(a, batal, lain, ids=ids)

        # Assert
        assert _pelanggaran(hasil) >= {
            (ViolationKind.SELECTION, DUPLICATE_PRODUCT),
            (ViolationKind.SESSION, SESSION_CANCELLED),
            (ViolationKind.REQUIREMENT, RUBRIC_VERSION),
        }
        assert hasil.valid is False

    def test_each_failed_requirement_is_its_own_violation(self, dua_produk):
        # Arrange
        a, _ = dua_produk
        lain = _rekaman(
            "Lain",
            [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5")],
            rubrik=uuid.UUID(int=99),
            judge_versi="judge-2",
        )

        # Act
        hasil, _ = _validasi(a, lain)

        # Assert
        syarat = {v.code for v in hasil.violations if v.kind is ViolationKind.REQUIREMENT}
        assert syarat == {RUBRIC_VERSION, JUDGE_MODEL}

    def test_one_session_can_have_several_reasons_each_tied_to_its_session(self, dua_produk):
        # Arrange
        a, _ = dua_produk
        rusak = _rekaman(
            "Rusak", [_nilai(1, "0.5", "0.5")], status=SessionStatus.FAILED, rubrik=None
        )

        # Act
        hasil, _ = _validasi(a, rusak)

        # Assert
        alasan = {v.code for v in hasil.violations if v.session_id == rusak.facts.session_id}
        assert alasan == {SESSION_RUN_FAILED, SESSION_NOT_GRADED}

    def test_an_ineligible_session_does_not_cause_a_false_requirement_rejection(self, dua_produk):
        # Arrange
        a, b = dua_produk
        rusak = _rekaman(
            "Rusak",
            [_nilai(1, "0.5", "0.5")],
            status=SessionStatus.FAILED,
            rubrik=uuid.UUID(int=99),
            judge_versi="judge-2",
        )

        # Act
        hasil, _ = _validasi(a, b, rusak)

        # Assert
        assert _pelanggaran(hasil) == {(ViolationKind.SESSION, SESSION_RUN_FAILED)}


class TestEmptyCaseSlice:
    @pytest.fixture
    def tanpa_irisan(self):
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], snapshot_tambahan=(_ref(2),))
        b = _rekaman("B", [_nilai(2, "0.5", "0.5")], snapshot_tambahan=(_ref(1),))
        return a, b

    def test_no_common_cases_is_a_violation_with_no_scope_to_offer(self, tanpa_irisan):
        # Act
        hasil, _ = _validasi(*tanpa_irisan)

        # Assert
        assert _pelanggaran(hasil) == {(ViolationKind.CASE_SLICE, NO_SHARED_CASES)}
        assert hasil.valid is False
        assert hasil.available_scopes == ()

    def test_the_empty_slice_is_reported_together_with_other_violations(self, tanpa_irisan):
        # Arrange
        a, b = tanpa_irisan
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

        # Act
        hasil, _ = _validasi(a, b, batal)

        # Assert
        assert _pelanggaran(hasil) == {
            (ViolationKind.SESSION, SESSION_CANCELLED),
            (ViolationKind.CASE_SLICE, NO_SHARED_CASES),
        }

    def test_a_scope_whose_cases_nobody_shares_counts_as_an_empty_slice(self, campuran):
        # Arrange: kasus dev ada, tetapi laporan Test tidak punya irisan bila hanya dev dijawab.
        a = _rekaman("A", [_nilai(3, "0.5", "0.5", SplitTag.DEV)])
        b = _rekaman("B", [_nilai(3, "0.5", "0.5", SplitTag.DEV)])

        # Act
        hasil, _ = _validasi(a, b, scope=ScoreScope.TEST)

        # Assert
        assert _pelanggaran(hasil) == {(ViolationKind.CASE_SLICE, NO_SHARED_CASES)}
        assert hasil.available_scopes == (ScoreScope.DEV,)


class TestWarnings:
    def test_different_repetitions_warn_without_blocking(self):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], repetisi=1)
        b = _rekaman("B", [_nilai(1, "0.5", "0.5")], repetisi=3)

        # Act
        hasil, _ = _validasi(a, b)

        # Assert
        assert hasil.valid is True
        assert (REPETITIONS_DIFFER, None) in _peringatan(hasil)

    def test_a_manual_answer_range_over_seven_days_warns_on_that_session(self):
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
        hasil, _ = _validasi(lama, otomatis)

        # Assert
        assert hasil.valid is True
        assert _peringatan(hasil) == {(MANUAL_ANSWER_RANGE_OVER_LIMIT, lama.facts.session_id)}

    def test_exactly_seven_days_does_not_warn(self):
        # Arrange
        pas = _rekaman(
            "Manual",
            [_nilai(1, "0.5", "0.5")],
            session_type=SessionType.MANUAL,
            dari=AWAL,
            sampai=AWAL + timedelta(days=7),
        )
        otomatis = _rekaman("Otomatis", [_nilai(1, "0.5", "0.5")])

        # Act
        hasil, _ = _validasi(pas, otomatis)

        # Assert
        assert hasil.warnings == ()

    def test_model_version_differences_and_trial_sessions_are_passed_on(self):
        # Arrange
        uji = _rekaman(
            "Uji", [_nilai(1, "0.5", "0.5")], purpose=SessionPurpose.TRIAL, model="model-1"
        )
        resmi = _rekaman("Resmi", [_nilai(1, "0.5", "0.5")], model="model-2")

        # Act
        hasil, _ = _validasi(uji, resmi)

        # Assert
        assert hasil.valid is True
        assert _peringatan(hasil) == {
            (MODEL_VERSION_DIFFERS, None),
            (TRIAL_SESSION, uji.facts.session_id),
        }
        assert all(w.message for w in hasil.warnings)


class TestAvailableScopes:
    def test_a_dev_only_slice_offers_only_dev_to_an_admin(self):
        # Arrange
        a = _rekaman("A", [_nilai(3, "0.5", "0.5", SplitTag.DEV)])
        b = _rekaman("B", [_nilai(3, "0.5", "0.5", SplitTag.DEV)])

        # Act
        hasil, _ = _validasi(a, b, scope=ScoreScope.DEV)

        # Assert
        assert hasil.valid is True
        assert hasil.available_scopes == (ScoreScope.DEV,)

    def test_a_mixed_slice_offers_every_scope_to_developer_ai(self, campuran):
        # Act
        hasil, _ = _validasi(*campuran, role=Role.DEVELOPER_AI, scope=ScoreScope.DEV_TEST)

        # Assert
        assert hasil.available_scopes == (ScoreScope.TEST, ScoreScope.DEV, ScoreScope.DEV_TEST)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    def test_viewer_and_contributor_only_get_test_even_when_dev_cases_exist(self, campuran, role):
        # Act
        hasil, _ = _validasi(*campuran, role=role)

        # Assert
        assert hasil.available_scopes == (ScoreScope.TEST,)

    def test_scopes_come_from_eligible_sessions_only(self, campuran):
        # Arrange: sesi batal yang cuma punya kasus test tidak boleh mengecilkan pilihan.
        a, b = campuran
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

        # Act
        hasil, _ = _validasi(a, b, batal, scope=ScoreScope.DEV_TEST)

        # Assert
        assert hasil.available_scopes == (ScoreScope.TEST, ScoreScope.DEV, ScoreScope.DEV_TEST)


class TestRoleAndLookup:
    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_a_scope_beyond_the_role_is_refused_before_any_session_is_read(
        self, dua_produk, role, scope
    ):
        # Arrange
        pembaca = PembacaPalsu(*dua_produk)
        validator = ReportValidator(pembaca)
        ids = tuple(r.facts.session_id for r in dua_produk)

        # Act
        with pytest.raises(ForbiddenError) as galat:
            validator.validate(
                role=role,
                session_ids=ids,
                score_scope=scope,
                measurement_filter=MeasurementFilter.ALL,
            )

        # Assert
        assert galat.value.status_code == 403
        assert galat.value.code == REPORT_SCOPE_FORBIDDEN
        assert pembaca.dipanggil == []

    def test_an_unknown_session_is_not_found(self, dua_produk):
        # Arrange
        a, b = dua_produk
        hilang = uuid.uuid4()

        # Act
        with pytest.raises(NotFoundError) as galat:
            _validasi(a, b, ids=(a.facts.session_id, hilang))

        # Assert
        assert galat.value.code == REPORT_SESSION_NOT_FOUND
        assert galat.value.details == {"missing": [str(hilang)]}

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.CONTRIBUTOR])
    def test_roles_that_may_read_cases_see_the_conflicting_case_codes(self, role):
        # Arrange
        a, b = _beda_versi_kasus_1()

        # Act
        hasil, _ = _validasi(a, b, role=role)

        # Assert
        (syarat,) = [v for v in hasil.violations if v.code == SUITE_VERSION]
        assert "CASE-001" in syarat.message

    @pytest.mark.parametrize("role", [Role.DEVELOPER_AI, Role.VIEWER])
    def test_roles_that_may_not_read_cases_get_a_message_without_case_codes(self, role):
        # Arrange
        a, b = _beda_versi_kasus_1()

        # Act
        hasil, _ = _validasi(a, b, role=role)

        # Assert
        (syarat,) = [v for v in hasil.violations if v.code == SUITE_VERSION]
        assert syarat.message
        assert "CASE-" not in syarat.message

    def test_the_measurement_filter_leaves_manual_sessions_out(self, dua_produk):
        # Arrange
        a, b = dua_produk
        manual = _rekaman("Manual", [_nilai(1, "0.5", "0.5")], session_type=SessionType.MANUAL)

        # Act
        hasil, _ = _validasi(a, b, manual, measurement_filter=MeasurementFilter.AUTOMATIC)

        # Assert
        assert hasil.valid is True


def _beda_versi_kasus_1():
    """Dua sesi yang menilai CASE-001 pada versi berbeda, jadi syarat suite gagal."""
    a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
    versi_dua = GradedCase(
        ref=_ref(1, versi=2), split=SplitTag.TEST, det=Decimal("0.5"), llm=Decimal("0.5")
    )
    return a, _rekaman("B", [versi_dua])
