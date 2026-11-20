"""Hak peran atas jenis kasus pada tren. PBI-17 SCRUM-84 SCRUM-331.

AC1, AC12: skor dev hanya untuk Admin dan Developer AI; Viewer dan Kontributor hanya Test.
"""

import pytest

from app.modules.reports.models import ScoreScope
from app.modules.reports.trend_policy import TREND_SCOPE_FORBIDDEN, ensure_can_view_trend
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role


@pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
@pytest.mark.parametrize("scope", list(ScoreScope))
def test_admin_and_developer_ai_may_view_every_case_type(role, scope):
    # Act
    hasil = ensure_can_view_trend(role, scope)

    # Assert
    assert hasil is None


@pytest.mark.parametrize("role", list(Role))
def test_every_role_may_view_the_test_only_trend(role):
    # Act
    hasil = ensure_can_view_trend(role, ScoreScope.TEST)

    # Assert
    assert hasil is None


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
@pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
def test_viewer_and_contributor_are_refused_trends_that_hold_dev_scores(role, scope):
    # Act
    with pytest.raises(ForbiddenError) as galat:
        ensure_can_view_trend(role, scope)

    # Assert
    assert galat.value.code == TREND_SCOPE_FORBIDDEN
    assert galat.value.status_code == 403
