import uuid
from unittest.mock import Mock

import pytest

from app.modules.runs import service
from app.modules.runs.models import SessionStatus, SessionType
from app.modules.runs.session_grade import DbGradingStore
from app.shared.security import Role


def test_only_matching_labels_trigger_grading_lookup(monkeypatch):
    unrelated, matching = uuid.uuid4(), uuid.uuid4()
    rows = [
        (session_id, label, SessionType.AUTOMATIC, SessionStatus.FINISHED, None)
        for session_id, label in [(unrelated, "Unrelated"), (matching, "MATCH")]
    ]
    monkeypatch.setattr(service.repository, "search_session_rows", Mock(return_value=rows))
    grading = Mock()

    hits = service.search_sessions(
        Mock(), "match", Role.CONTRIBUTOR, limit=20, has_results=grading.has_results
    )

    grading.has_results.assert_called_once_with(matching)
    assert len(hits) == 1


@pytest.mark.parametrize(
    ("role", "kind", "status", "available", "expected"),
    [
        (Role.ADMIN, SessionType.MANUAL, SessionStatus.FINALIZED, False, "manual_detail"),
        (Role.CONTRIBUTOR, SessionType.MANUAL, SessionStatus.FINALIZED, False, "manual_detail"),
        (Role.DEVELOPER_AI, SessionType.MANUAL, SessionStatus.FINALIZED, True, None),
        (Role.VIEWER, SessionType.MANUAL, SessionStatus.FINALIZED, True, None),
        (Role.ADMIN, SessionType.AUTOMATIC, SessionStatus.RUNNING, False, "automatic_detail"),
        (
            Role.DEVELOPER_AI,
            SessionType.AUTOMATIC,
            SessionStatus.RUNNING,
            False,
            "automatic_detail",
        ),
        (Role.VIEWER, SessionType.AUTOMATIC, SessionStatus.FINISHED, True, None),
        (Role.CONTRIBUTOR, SessionType.AUTOMATIC, SessionStatus.RUNNING, True, None),
        (Role.CONTRIBUTOR, SessionType.AUTOMATIC, SessionStatus.FINISHED, False, None),
        (Role.CONTRIBUTOR, SessionType.AUTOMATIC, SessionStatus.FINISHED, True, "grading_results"),
    ],
)
def test_session_destination(role, kind, status, available, expected):
    session_id = uuid.uuid4()
    lookup = Mock(return_value=available)
    assert service._search_destination(lookup, role, session_id, kind, status) == expected
    if (
        role == Role.CONTRIBUTOR
        and kind == SessionType.AUTOMATIC
        and status == SessionStatus.FINISHED
    ):
        lookup.assert_called_once_with(session_id)
    else:
        lookup.assert_not_called()


@pytest.mark.parametrize(
    ("persisted", "audit_runs", "expected"),
    [(True, [], True), (False, [object()], True), (False, [], False)],
)
def test_grading_availability_falls_back_to_audit(monkeypatch, persisted, audit_runs, expected):
    session_id = uuid.uuid4()
    db = Mock()
    db.scalar.return_value = uuid.uuid4() if persisted else None
    store = DbGradingStore(db)
    audit = Mock(return_value=audit_runs)
    monkeypatch.setattr(store, "_get_grading_runs_from_audit", audit)

    assert store.has_results(session_id) is expected
    if persisted:
        audit.assert_not_called()
    else:
        audit.assert_called_once_with(session_id)
