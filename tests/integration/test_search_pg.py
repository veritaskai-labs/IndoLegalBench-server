from tests.drilldown_qa_pg import DEV_AI, HANYA_POSTGRES, KONTRIBUTOR
from tests.modules.search.test_search import _search

pytestmark = HANYA_POSTGRES


def test_search_uses_live_case_and_session_permissions_on_postgres(dunia):
    developer = dunia.sebagai(DEV_AI)
    assert _search(developer, "PHK-001")["cases"] == []
    assert [hit["case_code"] for hit in _search(developer, "Bolehkah upah")["cases"]] == ["PHK-004"]
    automatic = _search(developer, "QA PBI-16")["sessions"]
    assert [(hit["id"], hit["destination"]) for hit in automatic] == [
        (str(dunia.otomatis.id), "automatic_detail")
    ]

    contributor = dunia.sebagai(KONTRIBUTOR)
    sessions = _search(contributor, "QA PBI-16")["sessions"]
    assert {hit["id"]: hit["destination"] for hit in sessions} == {
        str(dunia.otomatis.id): "grading_results",
        str(dunia.manual.id): "manual_detail",
    }
