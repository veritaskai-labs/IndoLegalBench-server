"""Acceptance tests for the global search, PBI-20 AC1-AC4. QA sub task SCRUM-353.

The SCRUM-351 tests already check the rules one by one. This file only adds the QA scenarios they
do not cover, all through GET /search as the search box calls it:

- AC4: one shared data set searched by all four roles, with the exact hits each role may get
- AC4: a manual session made by someone else is found by Admin and Contributor, never by the others
- AC4: results depend on the role, so the response must not be cached
- AC4: no field of a test case (legal reference, criteria, conclusion) gives Developer AI a hit
- AC2, AC4: Developer AI finds a dev case by its criteria and legal reference, and the hit carries
  no snippet of them
- AC4: a duplicate (draft, no tag) and a dev case that Admin retagged as test stay hidden from Developer AI
- AC1: users and audit entries are not searched
- Edge: a keyword of exactly 200 characters is accepted

Synthetic data only (L1, L3), no Veritask cases.
"""

import uuid

import pytest

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.grading.models import GradingRunStatus
from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role
from tests.legal_refs import rujukan_pasal
from tests.modules.cases.test_cases import _ganti, _setujui, _suite
from tests.modules.runs.test_session_list_endpoint import _sesi
from tests.modules.runs.test_session_list_endpoint import pembuat as pembuat
from tests.modules.search.test_search import EMPTY, _case, _grade, _search
from tests.modules.search.test_search import product as product

URL = "/search"
KEYWORD = "MatrixKeyword"


@pytest.fixture
def world(as_role, db_session, product, pembuat):
    """The same small data set for every role: two approved cases, one draft, two sessions.

    Built as Admin (who may write everything), then each test swaps the user.
    """
    client = as_role(Role.ADMIN)
    suite_id = _suite(client, f"{KEYWORD} Suite")
    dev_id = _case(
        client, suite_id, case_code="MTX-01", split_tag="dev", identity=_title("Dev case")
    )
    test_id = _case(
        client, suite_id, case_code="MTX-02", split_tag="test", identity=_title("Test case")
    )
    draft_id = _case(
        client, suite_id, case_code="MTX-03", split_tag="dev", identity=_title("Draft case")
    )
    _setujui(db_session, dev_id)
    _setujui(db_session, test_id)
    automatic = _sesi(
        db_session, product, pembuat, label=f"{KEYWORD} automatic", status=SessionStatus.FINISHED
    )
    _grade(db_session, automatic, GradingRunStatus.FINISHED)
    manual = _sesi(
        db_session,
        product,
        pembuat,
        label=f"{KEYWORD} manual",
        session_type=SessionType.MANUAL,
        status=SessionStatus.NOT_FILLED,
    )
    return {
        "client": client,
        "suite_id": suite_id,
        "cases": {"dev": dev_id, "test": test_id, "draft": draft_id},
        "automatic": str(automatic.id),
        "manual": str(manual.id),
    }


def _title(text: str) -> dict:
    return {"title": f"{KEYWORD} {text}", "question": "Synthetic question?"}


def _as(world, buat_pengguna, role: Role):
    _ganti(world["client"], buat_pengguna(role))
    return world["client"]


def _summary(body: dict) -> dict:
    """Case codes and session destinations, in the order the API returned them."""
    return {
        "suites": len(body["suites"]),
        "cases": [hit["case_code"] for hit in body["cases"]],
        "sessions": [(hit["session_type"], hit["destination"]) for hit in body["sessions"]],
    }


# --- AC4: the same data, four roles --------------------------------------------------------------

# Admin and Contributor read every case, whatever its tag or status. Developer AI gets only the
# approved dev case. A Contributor opens an automatic session through its grading results.
# Viewer gets nothing: the destinations for Viewer are still waiting for a product decision
# (see the note in service.search), so this pins today's behaviour, not a final rule.
EXPECTED_BY_ROLE = {
    Role.ADMIN: {
        "suites": 1,
        "cases": ["MTX-01", "MTX-02", "MTX-03"],
        "sessions": [("automatic", "automatic_detail"), ("manual", "manual_detail")],
    },
    Role.CONTRIBUTOR: {
        "suites": 1,
        "cases": ["MTX-01", "MTX-02", "MTX-03"],
        "sessions": [("automatic", "grading_results"), ("manual", "manual_detail")],
    },
    Role.DEVELOPER_AI: {
        "suites": 1,
        "cases": ["MTX-01"],
        "sessions": [("automatic", "automatic_detail")],
    },
    Role.VIEWER: {"suites": 0, "cases": [], "sessions": []},
}


@pytest.mark.parametrize("role", list(EXPECTED_BY_ROLE), ids=lambda role: role.value)
def test_each_role_gets_only_the_hits_its_role_allows(world, buat_pengguna, role):
    """Positive and negative in one table: the same keyword, a different answer per role."""
    # Arrange
    client = _as(world, buat_pengguna, role)

    # Act
    body = _search(client, KEYWORD)

    # Assert
    assert _summary(body) == EXPECTED_BY_ROLE[role]


@pytest.mark.parametrize("role", [Role.DEVELOPER_AI, Role.VIEWER], ids=lambda role: role.value)
def test_manual_session_of_another_user_is_hidden_from_roles_without_the_right(
    world, buat_pengguna, role
):
    """Negative: the manual session was made by someone else, and these roles cannot see it."""
    # Arrange
    client = _as(world, buat_pengguna, role)

    # Act
    body = _search(client, f"{KEYWORD} manual")

    # Assert
    assert body == EMPTY


@pytest.mark.parametrize("role", [Role.ADMIN, Role.CONTRIBUTOR], ids=lambda role: role.value)
def test_manual_session_of_another_user_is_found_by_admin_and_contributor(
    world, buat_pengguna, role
):
    """Positive: AC4 gives Admin and Contributor the manual sessions of other users."""
    # Arrange
    client = _as(world, buat_pengguna, role)

    # Act
    body = _search(client, f"{KEYWORD} manual")

    # Assert
    assert [(hit["id"], hit["destination"]) for hit in body["sessions"]] == [
        (world["manual"], "manual_detail")
    ]


def test_a_test_case_is_hidden_from_developer_ai_even_when_the_suite_is_found(world, buat_pengguna):
    """Edge: the suite name matches, so the suite shows, but the test case in it does not."""
    # Arrange
    client = _as(world, buat_pengguna, Role.DEVELOPER_AI)

    # Act
    body = _search(client, "MTX-02")

    # Assert
    assert body == EMPTY
    assert [hit["label"] for hit in _search(client, KEYWORD)["suites"]] == [f"{KEYWORD} Suite"]


# --- AC4: results follow the role, so they must not be cached ------------------------------------


@pytest.mark.parametrize("role", list(Role), ids=lambda role: role.value)
def test_search_response_is_never_cached(world, buat_pengguna, role):
    """Edge: a shared cache could hand one role's hits to another, so every role says no-store."""
    # Arrange
    client = _as(world, buat_pengguna, role)

    # Act
    found = client.get(URL, params={"q": KEYWORD})
    nothing = client.get(URL, params={"q": "NoSuchKeyword"})

    # Assert
    assert found.headers["cache-control"] == "private, no-store"
    assert nothing.headers["cache-control"] == "private, no-store"


# --- Edge: the length of the keyword -------------------------------------------------------------


def test_keyword_of_exactly_200_characters_is_accepted(as_role):
    """Edge: 201 is rejected by the SCRUM-351 tests, so 200 must still work."""
    # Arrange
    client = as_role(Role.ADMIN)

    # Act
    response = client.get(URL, params={"q": "a" * 200})

    # Assert
    assert response.status_code == 200
    assert response.json() == EMPTY


# --- AC4: the content of a test case never gives Developer AI a hit ------------------------------

# SCRUM-351 checks title, question, conclusion, code and id. These are the other searchable fields.
TEST_CASE_FIELDS = {
    "free text legal reference": "TestRefKeyword",
    "note on a legal reference link": "TestAboutKeyword",
    "must_contain": "TestMustKeyword",
    "must_not_contain": "TestMustNotKeyword",
}


@pytest.mark.parametrize("keyword", TEST_CASE_FIELDS.values(), ids=TEST_CASE_FIELDS.keys())
def test_no_field_of_a_test_case_gives_developer_ai_a_hit(
    as_role, db_session, buat_pengguna, keyword
):
    """Negative: the word is only in one field of an approved test case, and only Admin finds it."""
    # Arrange
    client = as_role(Role.ADMIN)
    case_id = _case(
        client,
        _suite(client),
        split_tag="test",
        legal_refs=[
            {"kind": "free_text", "free_text": "TestRefKeyword", "links": []},
            rujukan_pasal("151", about="TestAboutKeyword"),
        ],
        answer_criteria={
            "must_contain": ["TestMustKeyword"],
            "must_not_contain": ["TestMustNotKeyword"],
            "expected_conclusion": "Synthetic conclusion",
        },
    )
    _setujui(db_session, case_id)
    assert [hit["id"] for hit in _search(client, keyword)["cases"]] == [case_id]

    # Act
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))
    body = _search(client, keyword)

    # Assert
    assert body == EMPTY


# --- AC2, AC4: what Developer AI may find, and what the hit shows --------------------------------


@pytest.mark.parametrize("keyword", ["DevMustKeyword", "DevRefKeyword", "DevConclusionKeyword"])
def test_developer_ai_finds_a_dev_case_by_criteria_or_legal_reference_without_a_snippet(
    as_role, db_session, buat_pengguna, keyword
):
    """Edge: the word is only in the criteria or the legal reference, and the hit does not repeat it."""
    # Arrange
    client = as_role(Role.ADMIN)
    case_id = _case(
        client,
        _suite(client),
        split_tag="dev",
        legal_refs=[{"kind": "free_text", "free_text": "DevRefKeyword", "links": []}],
        answer_criteria={
            "must_contain": ["DevMustKeyword"],
            "must_not_contain": [],
            "expected_conclusion": "DevConclusionKeyword",
        },
    )
    _setujui(db_session, case_id)
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))

    # Act
    response = client.get(URL, params={"q": keyword})

    # Assert
    assert response.status_code == 200
    assert response.json()["cases"] == [
        {"id": case_id, "label": "PHK sepihak", "case_code": "PHK-001", "archived": False}
    ]
    assert keyword not in response.text


# --- AC4: cases that are not an approved dev case ------------------------------------------------


def test_duplicate_of_a_dev_case_is_hidden_from_developer_ai_while_the_original_is_found(
    as_role, db_session, buat_pengguna
):
    """Edge: the copy has the same title as the original, but it is a draft without a tag."""
    # Arrange
    client = as_role(Role.ADMIN)
    original_id = _case(
        client,
        _suite(client),
        split_tag="dev",
        identity={"title": "DuplicateKeyword case", "question": "Synthetic question?"},
    )
    _setujui(db_session, original_id)
    copy = client.post(f"/cases/{original_id}/duplicate")
    assert copy.status_code == 201, copy.text
    copy_id = copy.json()["id"]
    assert copy.json()["split_tag"] is None
    assert {hit["id"] for hit in _search(client, "DuplicateKeyword")["cases"]} == {
        original_id,
        copy_id,
    }

    # Act
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))
    body = _search(client, "DuplicateKeyword")

    # Assert
    assert [hit["id"] for hit in body["cases"]] == [original_id]


def test_dev_case_retagged_as_test_by_admin_is_hidden_from_developer_ai(
    as_role, db_session, buat_pengguna
):
    """Edge: Developer AI found the case while it was dev, and loses it once Admin moves it to test."""
    # Arrange
    client = as_role(Role.ADMIN)
    case_id = _case(
        client,
        _suite(client),
        split_tag="dev",
        identity={"title": "RetagKeyword case", "question": "Synthetic question?"},
    )
    _setujui(db_session, case_id)
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))
    assert [hit["id"] for hit in _search(client, "RetagKeyword")["cases"]] == [case_id]
    _ganti(client, buat_pengguna(Role.ADMIN))

    # Act
    retag = client.patch(f"/cases/{case_id}/split-tag", json={"split_tag": "test", "confirm": True})
    _ganti(client, buat_pengguna(Role.DEVELOPER_AI))
    body = _search(client, "RetagKeyword")

    # Assert
    assert retag.status_code == 200, retag.text
    assert body == EMPTY
    _ganti(client, buat_pengguna(Role.ADMIN))
    assert [hit["id"] for hit in _search(client, "RetagKeyword")["cases"]] == [case_id]


# --- AC1: only suites, cases and sessions are searched -------------------------------------------


def test_users_and_audit_entries_are_not_searched(as_role, db_session):
    """Negative: a user's name and email and an audit entry's text give no hit, even for Admin."""
    # Arrange
    client = as_role(Role.ADMIN)
    person = User(
        email="userkeyword.person@veritask.test", name="UserNameKeyword", role=Role.CONTRIBUTOR
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        AuditLog(
            actor_user_id=person.id,
            action="AuditActionKeyword",
            entity_type="suite",
            entity_id=uuid.uuid4(),
            reason="AuditReasonKeyword",
        )
    )
    db_session.commit()
    suite_id = _suite(client, "ControlKeyword Suite")

    # Act
    results = {
        term: _search(client, term)
        for term in (
            "UserNameKeyword",
            "userkeyword.person",
            "AuditActionKeyword",
            "AuditReasonKeyword",
        )
    }

    # Assert
    assert all(body == EMPTY for body in results.values()), results
    assert [hit["id"] for hit in _search(client, "ControlKeyword")["suites"]] == [suite_id]
