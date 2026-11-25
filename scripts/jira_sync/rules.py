"""Pure rules of the Jira status sync: which keys a PR names and where a ticket goes.

No network and no clock here, so every rule is a plain function of its inputs.
Statuses are Jira status ids, not names, because Jira can show names in another
language (ids read from GET /issue/SCRUM-253/transitions, 25 Nov 2026).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

TODO = "10000"
IN_PROGRESS = "10001"
IN_REVIEW = "10002"
DONE = "10003"
PO_REVIEW = "10033"
CLIENT_ACCEPTED = "10034"

_ORDER = [TODO, IN_PROGRESS, IN_REVIEW, DONE, PO_REVIEW, CLIENT_ACCEPTED]
_NAMES = {
    TODO: "To Do",
    IN_PROGRESS: "In Progress",
    IN_REVIEW: "In Review",
    DONE: "Done",
    PO_REVIEW: "PO Review",
    CLIENT_ACCEPTED: "Client Accepted",
}

WORK = "work"  # subtask or task: moved by PR events
STORY = "story"  # PBI: follows its children, Done only through the PO
OTHER = "other"

# SCRUM-<digits> that is not glued to letters before it, and not followed by
# another digit, a letter or a dot (SCRUM-27.6 is a test case id, not a ticket).
_KEY = re.compile(r"(?<![A-Za-z0-9])SCRUM-(\d+)(?![\dA-Za-z.])", re.IGNORECASE)

ACCEPT_WORD = "acc"
REMINDER_MARK = "[jira-sync reminder]"
REMIND_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class Ticket:
    key: str
    kind: str
    status: str
    parent: str | None


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created: datetime


@dataclass(frozen=True)
class Decision:
    to: str | None
    reason: str = ""


def status_name(status: str) -> str:
    return _NAMES.get(status, f"status {status}")


def keys_from_pr(branch: str, title: str) -> list[str]:
    """Keys in the branch name, then in the title, in order and without repeats."""
    keys: list[str] = []
    for text in (branch, title):
        for number in _KEY.findall(text):
            key = f"SCRUM-{number}"
            if key not in keys:
                keys.append(key)
    return keys


def target_for_pr(action: str, *, merged: bool, draft: bool) -> str | None:
    if action == "closed":
        return DONE if merged else IN_PROGRESS
    if action in {"opened", "reopened", "ready_for_review"} and not draft:
        return IN_REVIEW
    return None


def decide(ticket: Ticket, target: str, *, other_open_pr: bool, closing: bool = True) -> Decision:
    """Where one ticket goes for one event, or why it stays.

    `closing` marks the agreed exception to "never backwards": a PR closed without
    merge sends In Review back to In Progress. A new branch (closing=False) only
    starts a ticket that is still To Do.
    """
    if ticket.kind != WORK:
        return Decision(None, "not a subtask or task")
    if ticket.status not in _ORDER:
        return Decision(None, f"unknown status {ticket.status}")
    if ticket.status == target:
        return Decision(None, f"already {status_name(target)}")
    if other_open_pr and (target == DONE or (target == IN_PROGRESS and closing)):
        return Decision(None, "another open PR names it")
    if target == IN_PROGRESS:
        allowed_from = IN_REVIEW if closing else TODO
        if ticket.status in {allowed_from, TODO}:
            return Decision(IN_PROGRESS)
        return Decision(None, f"already {status_name(ticket.status)}")
    if _ORDER.index(target) < _ORDER.index(ticket.status):
        return Decision(None, f"already {status_name(ticket.status)}")
    return Decision(target)


def story_on_start(story: Ticket) -> str | None:
    return IN_PROGRESS if story.status == TODO else None


def story_after_done(story: Ticket, children: list[Ticket]) -> str | None:
    if story.status != IN_PROGRESS or not children:
        return None
    if all(child.status in {DONE, PO_REVIEW, CLIENT_ACCEPTED} for child in children):
        return PO_REVIEW
    return None


def po_review_action(
    entered: datetime, comments: list[Comment], po_account: str, now: datetime
) -> str | None:
    """ "done" on an ACC comment by the PO, "remind" once after 24 h, else None."""
    since = [c for c in comments if c.created >= entered]
    for c in since:
        words = c.body.strip().lower().replace(",", " ").split()
        if c.author == po_account and words and words[0] == ACCEPT_WORD:
            return "done"
    if now - entered >= REMIND_AFTER and not any(c.body.startswith(REMINDER_MARK) for c in since):
        return "remind"
    return None
