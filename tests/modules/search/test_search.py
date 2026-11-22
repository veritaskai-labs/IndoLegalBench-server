import uuid
from datetime import date

import pytest

from app.modules.cases.models import Case
from app.modules.grading.models import GradingRun, GradingRunKind, GradingRunStatus, RubricVersion
from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10
from tests.modules.cases.test_cases import _badan, _buat, _ganti, _setujui, _suite
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import AWAL, _sesi
from tests.modules.runs.test_session_list_endpoint import pembuat as pembuat

URL = "/search"
EMPTY = {"suites": [], "cases": [], "sessions": []}


def _search(client, term):
    response = client.get(URL, params={"q": term})
    assert response.status_code == 200, response.text
    return response.json()


def _case(client, suite_id, **fields):
    response = _buat(client, suite_id, **fields)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _grade(db_session, session, status):
    rubric = RubricVersion(
        version_no=1,
        effective_date=date(2026, 11, 1),
        judge_prompt_quality="Synthetic quality prompt",
        judge_prompt_format="Synthetic format prompt",
        created_by=session.created_by,
    )
    db_session.add(rubric)
    db_session.flush()
    db_session.add(
        GradingRun(
            session_id=session.id,
            rubric_version_id=rubric.id,
            kind=GradingRunKind.INITIAL,
            status=status,
            triggered_by=session.created_by,
            finished_at=AWAL if status == GradingRunStatus.FINISHED else None,
        )
    )
    db_session.commit()


@pytest.fixture
def product(db_session, pembuat):
    item = _otomatis("ProductDirectoryKeyword", created_by=pembuat.id)
    db_session.add(item)
    db_session.commit()
    return item


def test_search_requires_login(client):
    assert client.get(URL, params={"q": "suite"}).status_code == 401


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": " \t "}, {"q": "x" * 201}])
def test_search_rejects_missing_blank_or_long_query(as_role, params):
    assert as_role(Role.ADMIN).get(URL, params=params).status_code == 422


def test_grouped_hits_include_archived_items(as_role, db_session, product, pembuat):
    client = as_role(Role.ADMIN)
    suite_id = _suite(client, "Benchmark Suite")
    case_id = _case(
        client, suite_id, identity={"title": "Benchmark Case", "question": "Synthetic question?"}
    )
    assert client.post(f"/suites/{suite_id}/archive").status_code == 200
    automatic = _sesi(
        db_session,
        product,
        pembuat,
        label="Benchmark Automatic",
        status=SessionStatus.FINISHED,
        archived_at=AWAL,
        archived_by=pembuat.id,
    )
    manual = _sesi(
        db_session,
        product,
        pembuat,
        label="Benchmark Manual",
        session_type=SessionType.MANUAL,
        status=SessionStatus.NOT_FILLED,
    )

    assert _search(client, "  bEnChMaRk  ") == {
        "suites": [{"id": suite_id, "label": "Benchmark Suite", "archived": True}],
        "cases": [
            {"id": case_id, "label": "Benchmark Case", "case_code": "PHK-001", "archived": True}
        ],
        "sessions": [
            {
                "id": str(automatic.id),
                "label": "Benchmark Automatic",
                "archived": True,
                "session_type": "automatic",
                "destination": "automatic_detail",
            },
            {
                "id": str(manual.id),
                "label": "Benchmark Manual",
                "archived": False,
                "session_type": "manual",
                "destination": "manual_detail",
            },
        ],
    }


def test_case_fields_are_searchable_without_returning_the_content(as_role, db_session):
    client = as_role(Role.ADMIN)
    suite_id = _suite(client)
    case_id = _case(
        client,
        suite_id,
        identity={"title": "Example case", "question": "QuestionKeyword?"},
        legal_refs=[
            rujukan_ac10(),
            {"kind": "free_text", "free_text": "CitationKeyword", "links": []},
        ],
        answer_criteria={
            "must_contain": ["MustContainKeyword"],
            "must_not_contain": ["MustNotContainKeyword"],
            "expected_conclusion": "ConclusionKeyword",
        },
    )
    legacy_id = _case(client, suite_id, case_code="LEGACY-01")
    version = db_session.get(Case, uuid.UUID(legacy_id)).current_version
    version.content = {
        **version.content,
        "legal_refs": [
            {"regulation_type": "uu", "regulation_number": "99", "year": 2001, "pasal": "52"}
        ],
    }
    db_session.commit()

    for query in (
        "example case",
        "phk-001",
        case_id.upper(),
        "QuestionKeyword",
        "Pasal II Angka 5 UU 6/2023",
        "CitationKeyword",
        "MustContainKeyword",
        "MustNotContainKeyword",
        "ConclusionKeyword",
    ):
        assert _search(client, query)["cases"] == [
            {"id": case_id, "label": "Example case", "case_code": "PHK-001", "archived": False}
        ], query
    assert [hit["id"] for hit in _search(client, "Pasal 52 UU 99/2001")["cases"]] == [legacy_id]
    assert _search(client, case_id[:12]) == EMPTY


def test_search_excludes_deleted_suites_and_treats_wildcards_as_literal(
    as_role, db_session, product
):
    client = as_role(Role.ADMIN)
    suite_id = _suite(client, "DeletedSuiteKeyword")
    _case(client, suite_id, case_code="DELETED-CASE")
    assert client.delete(f"/suites/{suite_id}").status_code == 204
    literal_id = _suite(client, "Literal_% Suite")
    _suite(client, "LiteralXY Suite")

    assert _search(client, "DeletedSuiteKeyword") == EMPTY
    assert _search(client, "DELETED-CASE") == EMPTY
    assert _search(client, product.name) == EMPTY
    assert _search(client, "Literal_%")["suites"] == [
        {"id": literal_id, "label": "Literal_% Suite", "archived": False}
    ]


def test_developer_ai_searches_approved_dev_content_before_result_cap(
    as_role, buat_pengguna, db_session, product, pembuat
):
    client = as_role(Role.ADMIN)
    suite_id = _suite(client)
    for index in range(20):
        case_id = _case(
            client,
            suite_id,
            case_code=f"AAA-{index:02}",
            split_tag="test",
            identity={"title": "SharedKeyword SecretTitle", "question": "SecretQuestion?"},
            answer_criteria={"expected_conclusion": "SecretCriterion"},
        )
        _setujui(db_session, case_id)
    _case(client, suite_id, case_code="DRAFT-ONLY")
    visible_id = _case(
        client,
        suite_id,
        case_code="VISIBLE-01",
        identity={"title": "SharedKeyword", "question": "ApprovedDevQuestion?"},
    )
    _setujui(db_session, visible_id)
    assert client.post(f"/cases/{visible_id}/versions").status_code == 201
    _sesi(db_session, product, pembuat, label="AutomaticKeyword")
    _sesi(
        db_session,
        product,
        pembuat,
        label="ManualKeyword",
        session_type=SessionType.MANUAL,
        status=SessionStatus.NOT_FILLED,
    )

    admin_hits = _search(client, "SharedKeyword")["cases"]
    assert [hit["case_code"] for hit in admin_hits] == [f"AAA-{index:02}" for index in range(20)]

    _ganti(client, buat_pengguna(Role.CONTRIBUTOR))
    updated = client.put(
        f"/cases/{visible_id}",
        json=_badan(
            case_code="VISIBLE-01",
            identity={"title": "NewDraftKeyword", "question": "DraftQuestion?"},
        ),
    )
    # Only the author can edit the draft. The two roles use the same user ID here.
    assert updated.status_code == 200, updated.text
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))

    assert [hit["id"] for hit in _search(client, "SharedKeyword")["cases"]] == [visible_id]
    assert [hit["id"] for hit in _search(client, "ApprovedDevQuestion")["cases"]] == [visible_id]
    assert _search(client, "AutomaticKeyword")["sessions"][0]["destination"] == "automatic_detail"
    for hidden in (
        case_id,
        "AAA-19",
        "SecretTitle",
        "SecretQuestion",
        "SecretCriterion",
        "DRAFT-ONLY",
        "NewDraftKeyword",
        "ManualKeyword",
    ):
        assert _search(client, hidden) == EMPTY, hidden


@pytest.mark.parametrize(
    ("grading_status", "expected"),
    [(GradingRunStatus.FINISHED, True), (GradingRunStatus.QUEUED, False), (None, False)],
)
def test_contributor_automatic_hits_open_grading_results(
    as_role, db_session, product, pembuat, grading_status, expected
):
    automatic = _sesi(
        db_session, product, pembuat, label="AutoKeyword", status=SessionStatus.FINISHED
    )
    if grading_status is not None:
        _grade(db_session, automatic, grading_status)
    manual = _sesi(
        db_session,
        product,
        pembuat,
        label="ManualKeyword",
        session_type=SessionType.MANUAL,
        status=SessionStatus.NOT_FILLED,
    )
    client = as_role(Role.CONTRIBUTOR)

    hits = _search(client, "AutoKeyword")["sessions"]
    assert hits == (
        [
            {
                "id": str(automatic.id),
                "label": automatic.label,
                "archived": False,
                "session_type": "automatic",
                "destination": "grading_results",
            }
        ]
        if expected
        else []
    )
    assert client.get(f"/measurements/{automatic.id}/results").status_code == (
        200 if expected else 404
    )
    assert _search(client, "ManualKeyword")["sessions"][0]["id"] == str(manual.id)


def test_viewer_results_stay_empty_while_destinations_are_pending(
    as_role, buat_pengguna, db_session, product, pembuat
):
    client = as_role(Role.ADMIN)
    suite_id = _suite(client, "ViewerKeyword suite")
    _case(client, suite_id, identity={"title": "ViewerKeyword case", "question": "ViewerKeyword?"})
    _sesi(db_session, product, pembuat, label="ViewerKeyword automatic")
    _sesi(
        db_session,
        product,
        pembuat,
        label="ViewerKeyword manual",
        session_type=SessionType.MANUAL,
        status=SessionStatus.NOT_FILLED,
    )

    _ganti(client, buat_pengguna(Role.VIEWER))
    assert _search(client, "ViewerKeyword") == EMPTY
