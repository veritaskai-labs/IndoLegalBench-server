"""Event handlers: turn one GitHub event into Jira moves and a run report.

Each handler takes the clients as arguments (dependency injection), so tests pass
fakes. A Jira error on one ticket becomes a warning and the others still run.
"""

from dataclasses import dataclass, field
from datetime import datetime

from scripts.jira_sync import rules
from scripts.jira_sync.clients import JiraError
from scripts.jira_sync.rules import DONE, IN_PROGRESS, PO_REVIEW, status_name

BASE_BRANCH = "staging"
LIVE_PREFIX = "Live on staging:"


@dataclass
class Report:
    moved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def markdown(self, title: str) -> str:
        lines = [f"### Jira sync: {title}", ""]
        lines += [f"- {line}" for line in self.moved] or ["Nothing moved."]
        if self.notes:
            lines += ["", "Notes:", *[f"- {line}" for line in self.notes]]
        if self.warnings:
            lines += ["", "Warnings:", *[f"- {line}" for line in self.warnings]]
        return "\n".join(lines) + "\n"


def _move(jira, ticket: rules.Ticket, to: str, report: Report, why: str = "") -> bool:
    if not jira.move(ticket.key, to):
        report.warnings.append(f"{ticket.key}: no transition to {status_name(to)}")
        return False
    suffix = f" ({why})" if why else ""
    report.moved.append(f"{ticket.key}: {status_name(ticket.status)} → {status_name(to)}{suffix}")
    return True


def _start_story(jira, key: str | None, report: Report) -> None:
    story = jira.ticket(key) if key else None
    if story and story.kind == rules.STORY and rules.story_on_start(story):
        _move(jira, story, IN_PROGRESS, report)


def _finish_story(jira, key: str | None, report: Report, po_account: str) -> None:
    story = jira.ticket(key) if key else None
    if not story or story.kind != rules.STORY:
        return
    if not rules.story_after_done(story, jira.children(story.key)):
        return
    if _move(jira, story, PO_REVIEW, report) and po_account:
        jira.notify(
            story.key,
            f"{story.key} is ready for PO Review",
            "Every subtask is Done. Check the AC on dev, then comment starting with ACC.",
            [po_account],
        )


def _apply(jira, keys, target, report, *, other_open, closing=True, po_account="") -> None:
    for key in keys:
        try:
            ticket = jira.ticket(key)
            if ticket is None:
                report.warnings.append(f"{key}: not found in Jira")
                continue
            decision = rules.decide(
                ticket, target, other_open_pr=key in other_open, closing=closing
            )
            if decision.to is None:
                stay = f"left {status_name(ticket.status)} ({decision.reason})"
                is_story = decision.reason == "not a subtask or task"
                report.notes.append(f"{key}: {decision.reason}" if is_story else f"{key}: {stay}")
                continue
            if not _move(jira, ticket, decision.to, report):
                continue
            if decision.to in {IN_PROGRESS, rules.IN_REVIEW}:
                _start_story(jira, ticket.parent, report)
            if decision.to == DONE:
                _finish_story(jira, ticket.parent, report, po_account)
        except JiraError as error:
            report.warnings.append(f"{key}: Jira error ({error})")


def on_pr(
    jira, github, event: dict, repos: list[str], report: Report, *, po_account: str = ""
) -> None:
    pr = event["pull_request"]
    repo = event["repository"]["full_name"]
    if pr["head"]["repo"]["full_name"] != repo:
        report.notes.append("PR from a fork: skipped")
        return
    if pr["base"]["ref"] != BASE_BRANCH:
        report.notes.append(f"PR into {pr['base']['ref']}, not {BASE_BRANCH}: skipped")
        return
    keys = rules.keys_from_pr(pr["head"]["ref"], pr["title"])
    if not keys:
        report.notes.append("no Jira key in the branch name or title")
        return
    target = rules.target_for_pr(event["action"], merged=pr["merged"], draft=pr["draft"])
    if target is None:
        report.notes.append("draft or ignored action: nothing to do")
        return
    other_open = set()
    if event["action"] == "closed":
        other_open = github.open_pr_keys(repos, skip=(repo, pr["number"]))
    _apply(jira, keys, target, report, other_open=other_open, po_account=po_account)


def on_branch(jira, event: dict, report: Report) -> None:
    if event.get("ref_type") != "branch":
        return
    keys = rules.keys_from_pr(event["ref"], "")
    _apply(jira, keys, IN_PROGRESS, report, other_open=set(), closing=False)


def on_deploy(jira, github, event: dict, report: Report) -> None:
    run = event["workflow_run"]
    if run["conclusion"] != "success":
        report.notes.append("deploy did not succeed: nothing to do")
        return
    repo = event["repository"]["full_name"]
    prs = github.deployed_prs(repo, run["workflow_id"], run["id"], run["head_sha"])
    keys: list[str] = []
    for branch, title in prs:
        keys += [k for k in rules.keys_from_pr(branch, title) if k not in keys]
    marker = f"{LIVE_PREFIX} {repo}@{run['head_sha'][:7]}"
    for key in keys:
        try:
            ticket = jira.ticket(key)
            if ticket is None or ticket.kind != rules.WORK:
                continue
            if any(c.body.startswith(marker) for c in jira.comments(key)):
                continue
            jira.add_comment(key, f"{marker}, {run['html_url']}")
            report.moved.append(f"{key}: commented Live on staging")
        except JiraError as error:
            report.warnings.append(f"{key}: Jira error ({error})")


def on_po_review(jira, po_account: str, now: datetime, report: Report) -> None:
    if not po_account:
        report.warnings.append("JIRA_PO_ACCOUNT_ID is not set: PO review check skipped")
        return
    for key in jira.stories_in(PO_REVIEW):
        try:
            entered = jira.entered_status_at(key, PO_REVIEW)
            if entered is None:
                report.warnings.append(f"{key}: no PO Review entry in the changelog")
                continue
            action = rules.po_review_action(entered, jira.comments(key), po_account, now)
            story = rules.Ticket(key, rules.STORY, PO_REVIEW, None)
            if action == "done":
                _move(jira, story, DONE, report, "ACC by the PO")
            elif action == "remind":
                text = "Still in PO Review after 24 h. Comment starting with ACC once the AC is checked."
                jira.add_comment(key, f"{rules.REMINDER_MARK} {text}")
                jira.notify(key, f"{key} is waiting for PO Review", text, [po_account])
                report.notes.append(f"{key}: reminded the PO")
        except JiraError as error:
            report.warnings.append(f"{key}: Jira error ({error})")
