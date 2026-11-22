"""Hak membuat laporan menurut peran. PBI-15 SCRUM-79 SCRUM-313.

AC7, AC9, AC14. Murni, tanpa database.
"""

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.creation_policy import (
    REPORT_LABEL_MODE_FORBIDDEN,
    REPORT_SCOPE_FORBIDDEN,
    available_scopes,
    ensure_can_create,
)
from app.modules.reports.models import ProductLabelMode, ScoreScope
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role

NAMA_ASLI = ProductLabelMode.REAL_NAME
KODE = ProductLabelMode.CODE


class TestScope:
    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    def test_viewer_and_contributor_may_create_test_reports(self, role):
        # Act and Assert
        ensure_can_create(role, ScoreScope.TEST, NAMA_ASLI)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_viewer_and_contributor_cannot_create_reports_with_dev_scores(self, role, scope):
        # Act
        with pytest.raises(ForbiddenError) as galat:
            ensure_can_create(role, scope, NAMA_ASLI)

        # Assert
        assert galat.value.status_code == 403
        assert galat.value.code == REPORT_SCOPE_FORBIDDEN

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    @pytest.mark.parametrize("scope", list(ScoreScope))
    def test_admin_and_developer_ai_may_choose_any_scope(self, role, scope):
        # Act and Assert
        ensure_can_create(role, scope, NAMA_ASLI)

    def test_an_unknown_role_is_refused(self):
        # Act
        with pytest.raises(ForbiddenError) as galat:
            ensure_can_create("intern", ScoreScope.TEST, NAMA_ASLI)

        # Assert
        assert galat.value.code == REPORT_SCOPE_FORBIDDEN


class TestLabelMode:
    def test_admin_may_hide_product_names_behind_codes(self):
        # Act and Assert
        ensure_can_create(Role.ADMIN, ScoreScope.TEST, KODE)

    @pytest.mark.parametrize("role", [Role.DEVELOPER_AI, Role.VIEWER, Role.CONTRIBUTOR])
    def test_only_admin_may_choose_codes(self, role):
        # Act
        with pytest.raises(ForbiddenError) as galat:
            ensure_can_create(role, ScoreScope.TEST, KODE)

        # Assert
        assert galat.value.status_code == 403
        assert galat.value.code == REPORT_LABEL_MODE_FORBIDDEN

    @pytest.mark.parametrize("role", list(Role))
    def test_real_names_are_the_default_for_everyone(self, role):
        # Act and Assert
        ensure_can_create(role, ScoreScope.TEST, NAMA_ASLI)

    def test_scope_is_checked_before_label_mode(self):
        # Act
        with pytest.raises(ForbiddenError) as galat:
            ensure_can_create(Role.VIEWER, ScoreScope.DEV, KODE)

        # Assert
        assert galat.value.code == REPORT_SCOPE_FORBIDDEN


class TestAvailableScopes:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_a_slice_with_only_dev_cases_offers_only_dev(self, role):
        # Act
        hasil = available_scopes(role, {SplitTag.DEV})

        # Assert
        assert hasil == (ScoreScope.DEV,)

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_a_slice_with_only_test_cases_offers_only_test(self, role):
        # Act
        hasil = available_scopes(role, {SplitTag.TEST})

        # Assert
        assert hasil == (ScoreScope.TEST,)

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_a_slice_with_both_kinds_offers_every_scope(self, role):
        # Act
        hasil = available_scopes(role, {SplitTag.DEV, SplitTag.TEST})

        # Assert
        assert hasil == (ScoreScope.TEST, ScoreScope.DEV, ScoreScope.DEV_TEST)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    def test_viewer_and_contributor_only_ever_get_test(self, role):
        # Act
        hasil = available_scopes(role, {SplitTag.DEV, SplitTag.TEST})

        # Assert
        assert hasil == (ScoreScope.TEST,)

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    def test_viewer_gets_nothing_when_the_slice_has_only_dev_cases(self, role):
        # Act
        hasil = available_scopes(role, {SplitTag.DEV})

        # Assert
        assert hasil == ()

    def test_an_empty_slice_offers_nothing(self):
        # Act
        hasil = available_scopes(Role.ADMIN, set())

        # Assert
        assert hasil == ()

    def test_an_unknown_role_gets_nothing(self):
        # Act
        hasil = available_scopes("intern", {SplitTag.TEST})

        # Assert
        assert hasil == ()
