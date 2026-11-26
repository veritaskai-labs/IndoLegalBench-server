"""Small Jira Cloud and GitHub REST clients, standard library only.

Both take an `opener` with the urllib.request.urlopen signature, so tests can
answer with fake responses and the workflow needs no extra dependency.
Credentials only go into the Authorization header and are never printed.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from scripts.jira_sync import rules
from scripts.jira_sync.rules import Comment, Ticket

TIMEOUT_SECONDS = 20
_SUBTASK_TYPE = "10002"
_TASK_TYPE = "10003"
_STORY_TYPE = "10004"

Opener = Callable[..., Any]


class JiraError(Exception):
    """A Jira or GitHub call failed; the caller turns it into a warning."""


def _call(opener: Opener, method: str, url: str, headers: dict, body: dict | None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise JiraError(f"{method} {url.split('?')[0]} returned {error.code}") from error
    except urllib.error.URLError as error:
        raise JiraError(f"{method} {url.split('?')[0]} failed: {error.reason}") from error
    return json.loads(raw) if raw else None


def _jira_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")


# Jira comments arrive as ADF (a JSON tree). Enter starts a new block and
# Shift+Enter is a hardBreak node; both become a newline so words never glue.
_BLOCKS = {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}


def _adf_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "hardBreak":
        return "\n"
    text = node.get("text", "") + "".join(_adf_text(c) for c in node.get("content", []))
    return text + "\n" if node.get("type") in _BLOCKS else text


def _adf(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


class JiraClient:
    def __init__(
        self, base_url: str, email: str, token: str, opener: Opener = urllib.request.urlopen
    ):
        self._api = base_url.rstrip("/") + "/rest/api/3"
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._opener = opener

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        return _call(self._opener, method, self._api + path, self._headers, body)

    def _ticket(self, raw: dict) -> Ticket:
        fields = raw["fields"]
        issue_type = fields["issuetype"]
        if issue_type.get("subtask") or issue_type["id"] in {_SUBTASK_TYPE, _TASK_TYPE}:
            kind = rules.WORK
        elif issue_type["id"] == _STORY_TYPE:
            kind = rules.STORY
        else:
            kind = rules.OTHER
        parent = (fields.get("parent") or {}).get("key")
        return Ticket(raw["key"], kind, fields["status"]["id"], parent)

    def ticket(self, key: str) -> Ticket | None:
        try:
            raw = self._request("GET", f"/issue/{key}?fields=issuetype,status,parent")
        except JiraError as error:
            if error.__cause__ is not None and getattr(error.__cause__, "code", None) == 404:
                return None
            raise
        return self._ticket(raw)

    def move(self, key: str, status: str) -> bool:
        transitions = self._request("GET", f"/issue/{key}/transitions")["transitions"]
        for transition in transitions:
            if transition["to"]["id"] == status:
                self._request(
                    "POST", f"/issue/{key}/transitions", {"transition": {"id": transition["id"]}}
                )
                return True
        return False

    def _search(self, jql: str, fields: list[str]) -> list[dict]:
        return self._request(
            "POST", "/search/jql", {"jql": jql, "fields": fields, "maxResults": 100}
        )["issues"]

    def children(self, story_key: str) -> list[Ticket]:
        found = self._search(f"parent = {story_key}", ["issuetype", "status", "parent"])
        return [self._ticket(raw) for raw in found]

    def stories_in(self, status: str) -> list[str]:
        return [
            raw["key"]
            for raw in self._search(f"issuetype = Story AND status = {status}", ["status"])
        ]

    def comments(self, key: str) -> list[Comment]:
        # Newest first, so the latest ACC is read even on a story with 100+ comments.
        raw = self._request("GET", f"/issue/{key}/comment?maxResults=100&orderBy=-created")
        return [
            Comment(
                c["author"]["accountId"], _adf_text(c["body"]).strip(), _jira_time(c["created"])
            )
            for c in raw["comments"]
        ]

    def add_comment(self, key: str, text: str) -> None:
        self._request("POST", f"/issue/{key}/comment", {"body": _adf(text)})

    def notify(self, key: str, subject: str, text: str, account_ids: list[str]) -> None:
        to = {"users": [{"accountId": account} for account in account_ids]}
        self._request(
            "POST", f"/issue/{key}/notify", {"subject": subject, "textBody": text, "to": to}
        )

    def entered_status_at(self, key: str, status: str) -> datetime | None:
        entered = None
        start = 0
        while True:
            page = self._request("GET", f"/issue/{key}/changelog?startAt={start}&maxResults=100")
            for history in page["values"]:
                if any(i["field"] == "status" and i.get("to") == status for i in history["items"]):
                    entered = _jira_time(history["created"])
            if page.get("isLast", True):
                return entered
            start += len(page["values"])


class GitHubClient:
    _API = "https://api.github.com"

    def __init__(self, token: str, opener: Opener = urllib.request.urlopen):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._opener = opener

    def _get(self, path: str, **query: Any) -> Any:
        url = self._API + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return _call(self._opener, "GET", url, self._headers, None)

    def open_pr_keys(self, repos: list[str], skip: tuple[str, int]) -> set[str]:
        """Keys named by every open PR into staging in the given repos, except `skip`."""
        keys: set[str] = set()
        for repo in repos:
            for pr in self._get(f"/repos/{repo}/pulls", state="open", base="staging", per_page=100):
                if (repo, pr["number"]) != skip:
                    keys.update(rules.keys_from_pr(pr["head"]["ref"], pr["title"]))
        return keys

    def deployed_prs(
        self, repo: str, workflow_id: int, run_id: int, head_sha: str
    ) -> list[tuple[str, str]]:
        """Branch and title of every PR merged into staging since the previous successful deploy."""
        runs = self._get(
            f"/repos/{repo}/actions/workflows/{workflow_id}/runs",
            branch="staging",
            status="success",
            per_page=10,
        )["workflow_runs"]
        previous = next((r["head_sha"] for r in runs if r["id"] != run_id), None)
        if previous is None:
            return []
        commits = self._get(f"/repos/{repo}/compare/{previous}...{head_sha}")["commits"]
        prs: list[tuple[str, str]] = []
        for commit in commits:
            for pr in self._get(f"/repos/{repo}/commits/{commit['sha']}/pulls"):
                merged_into_staging = pr["merged_at"] and pr["base"]["ref"] == "staging"
                entry = (pr["head"]["ref"], pr["title"])
                if merged_into_staging and entry not in prs:
                    prs.append(entry)
        return prs
