import uuid
from unittest.mock import Mock

from app.modules.runs import service
from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role


def test_only_matching_labels_trigger_grading_lookup(monkeypatch):
    unrelated, matching = uuid.uuid4(), uuid.uuid4()
    rows = [
        (session_id, label, SessionType.AUTOMATIC, SessionStatus.FINISHED, None)
        for session_id, label in [(unrelated, "Unrelated"), (matching, "MATCH")]
    ]
    monkeypatch.setattr(service.repository, "search_session_rows", Mock(return_value=rows))
    grading = Mock()
    monkeypatch.setattr(service, "DbGradingStore", Mock(return_value=grading))

    hits = service.search_sessions(Mock(), "match", Role.CONTRIBUTOR, limit=20)

    grading.has_results.assert_called_once_with(matching)
    assert len(hits) == 1
