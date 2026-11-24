from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.modules.cases import service


def test_question_match_skips_citation_rendering(monkeypatch):
    render = Mock(return_value=[])
    monkeypatch.setattr(service, "_legal_refs_read", render)
    case = SimpleNamespace(id=uuid4(), case_code="DEV-01")
    version = SimpleNamespace(content={"question": "Where is the contract?"})

    assert service._matches_case("contract", case, version, "Case", Mock())
    render.assert_not_called()
