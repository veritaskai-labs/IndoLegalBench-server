"""Jira and GitHub REST clients of the Jira status sync (SCRUM-377).

Isolation: every client takes an `opener` (the urllib.request.urlopen signature).
FakeHttp below records each request and answers from a route table, so no test
touches the network, and the tests can check the exact URL, method, headers and body.
"""

import base64
import io
import json
import urllib.error
from datetime import UTC, datetime

import pytest

from scripts.jira_sync import rules
from scripts.jira_sync.clients import GitHubClient, JiraClient, JiraError
from scripts.jira_sync.rules import DONE, IN_REVIEW, PO_REVIEW


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes  # {(method, url prefix): payload or Exception}
        self.calls = []

    def __call__(self, request, timeout):
        url = request.full_url
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, url, body, dict(request.header_items())))
        for (route_method, prefix), answer in self.routes.items():
            if route_method == method and url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return io.BytesIO(json.dumps(answer).encode() if answer is not None else b"")
        raise AssertionError(f"unexpected request {method} {url}")


BASE = "https://example.atlassian.net"
API = BASE + "/rest/api/3"


def http_error(code):
    return urllib.error.HTTPError("u", code, "error", {}, io.BytesIO(b"{}"))


def issue(key, type_id, status, parent=None, subtask=False):
    fields = {"issuetype": {"id": type_id, "subtask": subtask}, "status": {"id": status}}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "fields": fields}


def jira(routes):
    http = FakeHttp(routes)
    return JiraClient(BASE + "/", "bot@example.test", "token-123", opener=http), http


# --- Jira -------------------------------------------------------------------


def test_ticket_reads_kind_status_and_parent_with_basic_auth():
    client, http = jira(
        {
            ("GET", API + "/issue/SCRUM-321"): issue(
                "SCRUM-321", "10002", IN_REVIEW, "SCRUM-79", True
            )
        }
    )
    ticket = client.ticket("SCRUM-321")
    assert ticket == rules.Ticket("SCRUM-321", rules.WORK, IN_REVIEW, "SCRUM-79")
    headers = http.calls[0][3]
    expected = base64.b64encode(b"bot@example.test:token-123").decode()
    assert headers["Authorization"] == f"Basic {expected}"


@pytest.mark.parametrize(
    ("type_id", "subtask", "kind"),
    [("10003", False, rules.WORK), ("10004", False, rules.STORY), ("10001", False, rules.OTHER)],
)
def test_ticket_kind_from_the_issue_type(type_id, subtask, kind):
    client, _ = jira(
        {("GET", API + "/issue/SCRUM-1"): issue("SCRUM-1", type_id, DONE, None, subtask)}
    )
    assert client.ticket("SCRUM-1").kind == kind


def test_missing_ticket_is_none_and_other_errors_raise():
    client, _ = jira({("GET", API + "/issue/SCRUM-9"): http_error(404)})
    assert client.ticket("SCRUM-9") is None
    client, _ = jira({("GET", API + "/issue/SCRUM-9"): http_error(500)})
    with pytest.raises(JiraError, match="500"):
        client.ticket("SCRUM-9")


def test_network_error_becomes_a_jira_error():
    client, _ = jira({("GET", API + "/issue/SCRUM-9"): urllib.error.URLError("down")})
    with pytest.raises(JiraError, match="down"):
        client.ticket("SCRUM-9")


def test_move_picks_the_transition_by_target_status_id_not_by_name():
    transitions = {
        "transitions": [
            {"id": "31", "name": "Sedang direview", "to": {"id": IN_REVIEW}},
            {"id": "41", "name": "Selesai", "to": {"id": DONE}},
        ]
    }
    client, http = jira(
        {
            ("GET", API + "/issue/SCRUM-321/transitions"): transitions,
            ("POST", API + "/issue/SCRUM-321/transitions"): None,
        }
    )
    assert client.move("SCRUM-321", DONE) is True
    assert http.calls[-1][2] == {"transition": {"id": "41"}}


def test_move_without_a_matching_transition_returns_false():
    client, http = jira({("GET", API + "/issue/SCRUM-1/transitions"): {"transitions": []}})
    assert client.move("SCRUM-1", PO_REVIEW) is False
    assert len(http.calls) == 1


def test_children_searches_by_parent():
    found = {"issues": [issue("SCRUM-2", "10002", DONE, "SCRUM-1", True)]}
    client, http = jira({("POST", API + "/search/jql"): found})
    assert [t.key for t in client.children("SCRUM-1")] == ["SCRUM-2"]
    assert http.calls[0][2]["jql"] == "parent = SCRUM-1"


def test_stories_in_status():
    client, http = jira({("POST", API + "/search/jql"): {"issues": [{"key": "SCRUM-79"}]}})
    assert client.stories_in(PO_REVIEW) == ["SCRUM-79"]
    assert http.calls[0][2]["jql"] == f"issuetype = Story AND status = {PO_REVIEW}"


def test_comments_are_read_as_plain_text():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "ACC"}, {"type": "text", "text": " ok"}],
            }
        ],
    }
    payload = {
        "comments": [
            {"author": {"accountId": "po"}, "body": adf, "created": "2026-11-26T10:00:00.000+0700"}
        ]
    }
    client, _ = jira({("GET", API + "/issue/SCRUM-79/comment"): payload})
    [comment] = client.comments("SCRUM-79")
    assert comment.author == "po"
    assert comment.body == "ACC ok"
    assert comment.created == datetime(2026, 11, 26, 3, 0, tzinfo=UTC)


def _comment_payload(adf):
    created = "2026-11-26T10:00:00.000+0700"
    return {"comments": [{"author": {"accountId": "po"}, "body": adf, "created": created}]}


def _para(*nodes):
    return {"type": "paragraph", "content": list(nodes)}


def _text(value):
    return {"type": "text", "text": value}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        # "ACC", Enter, a note: Jira stores two paragraphs (review on #172)
        ([_para(_text("ACC")), _para(_text("Sudah dicek di dev"))], "ACC\nSudah dicek di dev"),
        # Shift+Enter: one paragraph with a hardBreak node
        ([_para(_text("ACC"), {"type": "hardBreak"}, _text("ok"))], "ACC\nok"),
        # a mention has no text of its own
        ([_para({"type": "mention", "attrs": {"text": "@Roben"}}, _text(" ACC"))], "ACC"),
    ],
)
def test_comment_paragraphs_and_line_breaks_stay_separate(content, expected):
    client, _ = jira(
        {
            ("GET", API + "/issue/SCRUM-79/comment"): _comment_payload(
                {"type": "doc", "content": content}
            )
        }
    )
    assert client.comments("SCRUM-79")[0].body == expected


def test_comments_are_read_newest_first():
    # with more than 100 comments the newest ones, where the ACC is, must still be read
    client, http = jira({("GET", API + "/issue/SCRUM-79/comment"): {"comments": []}})
    client.comments("SCRUM-79")
    assert "orderBy=-created" in http.calls[0][1]


def test_add_comment_sends_an_adf_paragraph():
    client, http = jira({("POST", API + "/issue/SCRUM-1/comment"): {"id": "1"}})
    client.add_comment("SCRUM-1", "Live on staging")
    body = http.calls[0][2]["body"]
    assert body["content"][0]["content"][0]["text"] == "Live on staging"


def test_notify_sends_to_the_given_accounts():
    client, http = jira({("POST", API + "/issue/SCRUM-79/notify"): None})
    client.notify("SCRUM-79", "PO Review", "please check", ["po"])
    assert http.calls[0][2] == {
        "subject": "PO Review",
        "textBody": "please check",
        "to": {"users": [{"accountId": "po"}]},
    }


def test_entered_status_at_reads_the_newest_matching_changelog_entry():
    log = {
        "values": [
            {
                "created": "2026-11-20T10:00:00.000+0700",
                "items": [{"field": "status", "to": PO_REVIEW}],
            },
            {
                "created": "2026-11-25T10:00:00.000+0700",
                "items": [{"field": "status", "to": PO_REVIEW}],
            },
            {"created": "2026-11-26T10:00:00.000+0700", "items": [{"field": "summary", "to": "x"}]},
        ],
        "isLast": True,
    }
    client, _ = jira({("GET", API + "/issue/SCRUM-79/changelog"): log})
    assert client.entered_status_at("SCRUM-79", PO_REVIEW) == datetime(
        2026, 11, 25, 3, 0, tzinfo=UTC
    )
    client, _ = jira({("GET", API + "/issue/SCRUM-1/changelog"): {"values": [], "isLast": True}})
    assert client.entered_status_at("SCRUM-1", PO_REVIEW) is None


# --- GitHub -----------------------------------------------------------------

GH = "https://api.github.com"


def github(routes):
    http = FakeHttp(routes)
    return GitHubClient("gh-token", opener=http), http


def test_open_pr_keys_across_repos_skip_the_current_pr():
    client, http = github(
        {
            ("GET", GH + "/repos/org/server/pulls"): [
                {"number": 5, "head": {"ref": "feat/pbi1-SCRUM-1-SCRUM-2-x"}, "title": ""},
                {"number": 6, "head": {"ref": "feat/pbi1-SCRUM-1-SCRUM-3-x"}, "title": ""},
            ],
            ("GET", GH + "/repos/org/client"): [
                {"number": 9, "head": {"ref": "chore/x"}, "title": "fix: SCRUM-4"},
            ],
        }
    )
    keys = client.open_pr_keys(["org/server", "org/client"], skip=("org/server", 5))
    assert keys == {"SCRUM-1", "SCRUM-3", "SCRUM-4"}
    assert http.calls[0][3]["Authorization"] == "Bearer gh-token"
    assert "state=open" in http.calls[0][1]


def test_deployed_prs_between_the_last_two_successful_deploys():
    client, _ = github(
        {
            ("GET", GH + "/repos/org/server/actions/workflows/7/runs"): {
                "workflow_runs": [
                    {"id": 100, "head_sha": "new", "conclusion": "success"},
                    {"id": 90, "head_sha": "old", "conclusion": "success"},
                ]
            },
            ("GET", GH + "/repos/org/server/compare/old...new"): {
                "commits": [{"sha": "c1"}, {"sha": "c2"}]
            },
            ("GET", GH + "/repos/org/server/commits/c1/pulls"): [
                {
                    "merged_at": "x",
                    "base": {"ref": "staging"},
                    "head": {"ref": "feat/pbi1-SCRUM-1-SCRUM-2-a"},
                    "title": "t",
                }
            ],
            ("GET", GH + "/repos/org/server/commits/c2/pulls"): [
                {
                    "merged_at": None,
                    "base": {"ref": "staging"},
                    "head": {"ref": "x/SCRUM-8"},
                    "title": "",
                },
                {
                    "merged_at": "x",
                    "base": {"ref": "staging"},
                    "head": {"ref": "feat/pbi1-SCRUM-1-SCRUM-2-a"},
                    "title": "t",
                },
            ],
        }
    )
    prs = client.deployed_prs("org/server", workflow_id=7, run_id=100, head_sha="new")
    assert prs == [("feat/pbi1-SCRUM-1-SCRUM-2-a", "t")]


def test_first_ever_deploy_has_no_range_and_reports_nothing():
    client, _ = github(
        {
            ("GET", GH + "/repos/org/server/actions/workflows/7/runs"): {
                "workflow_runs": [{"id": 100, "head_sha": "new", "conclusion": "success"}]
            }
        }
    )
    assert client.deployed_prs("org/server", workflow_id=7, run_id=100, head_sha="new") == []


def test_deployed_prs_filters_success_itself_instead_of_trusting_the_status_filter():
    # 26 Nov: GitHub's status filter (success, and completed too) left out the successful
    # d60cab8 deploy, so the range started one deploy too early and SCRUM-347 got a second
    # "Live on staging". Ask for the plain list and keep the successful runs here.
    client, http = github(
        {
            ("GET", GH + "/repos/org/server/actions/workflows/7/runs"): {
                "workflow_runs": [
                    {"id": 100, "head_sha": "new", "conclusion": "success"},
                    {"id": 95, "head_sha": "broken", "conclusion": "failure"},
                    {"id": 92, "head_sha": "stopped", "conclusion": "cancelled"},
                    {"id": 90, "head_sha": "old", "conclusion": "success"},
                    {"id": 80, "head_sha": "older", "conclusion": "success"},
                ]
            },
            ("GET", GH + "/repos/org/server/compare/old...new"): {"commits": []},
        }
    )
    assert client.deployed_prs("org/server", workflow_id=7, run_id=100, head_sha="new") == []
    runs_url = http.calls[0][1]
    assert "status=" not in runs_url
    assert "branch=staging" in runs_url


def test_no_earlier_successful_deploy_reports_nothing():
    # A run still in progress has no conclusion yet; it must not start the range either.
    client, http = github(
        {
            ("GET", GH + "/repos/org/server/actions/workflows/7/runs"): {
                "workflow_runs": [
                    {"id": 101, "head_sha": "running", "conclusion": None},
                    {"id": 100, "head_sha": "new", "conclusion": "success"},
                    {"id": 95, "head_sha": "broken", "conclusion": "failure"},
                ]
            }
        }
    )
    assert client.deployed_prs("org/server", workflow_id=7, run_id=100, head_sha="new") == []
    assert len(http.calls) == 1


def test_entered_status_at_follows_changelog_pages():
    first = {
        "values": [
            {
                "created": "2026-11-20T10:00:00.000+0700",
                "items": [{"field": "status", "to": PO_REVIEW}],
            }
        ],
        "isLast": False,
    }
    second = {"values": [], "isLast": True}
    client, http = jira(
        {
            ("GET", API + "/issue/SCRUM-79/changelog?startAt=1"): second,
            ("GET", API + "/issue/SCRUM-79/changelog?startAt=0"): first,
        }
    )
    assert client.entered_status_at("SCRUM-79", PO_REVIEW) == datetime(
        2026, 11, 20, 3, 0, tzinfo=UTC
    )
    assert len(http.calls) == 2


def test_comment_text_skips_non_text_nodes():
    payload = {
        "comments": [
            {
                "author": {"accountId": "po"},
                "body": {
                    "type": "doc",
                    "content": [{"type": "mention"}, "stray", {"type": "text", "text": "ACC"}],
                },
                "created": "2026-11-26T10:00:00.000+0700",
            }
        ]
    }
    client, _ = jira({("GET", API + "/issue/SCRUM-79/comment"): payload})
    assert client.comments("SCRUM-79")[0].body == "ACC"
