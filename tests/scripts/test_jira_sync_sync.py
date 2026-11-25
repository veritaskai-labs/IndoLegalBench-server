"""Event handlers of the Jira status sync (SCRUM-377).

Isolation: FakeJira and FakeGitHub stand in for the REST clients (the clients have
their own tests with a fake HTTP opener), so these tests only check which tickets
move and what the run summary says.
"""

from datetime import UTC, datetime, timedelta

from scripts.jira_sync import rules, sync
from scripts.jira_sync.clients import JiraError
from scripts.jira_sync.rules import DONE, IN_PROGRESS, IN_REVIEW, PO_REVIEW, TODO, Comment, Ticket

REPO = "org/server"
REPOS = ["org/server", "org/client"]
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class FakeJira:
    def __init__(self, *tickets, comments=None, entered=None, fail=()):
        self.tickets = {t.key: t for t in tickets}
        self.moves = []
        self.comments_added = []
        self.notified = []
        self._comments = comments or {}
        self._entered = entered or {}
        self.fail = set(fail)

    def ticket(self, key):
        if key in self.fail:
            raise JiraError("boom")
        return self.tickets.get(key)

    def move(self, key, status):
        if status == "no-transition":
            return False
        self.moves.append((key, status))
        old = self.tickets[key]
        self.tickets[key] = Ticket(old.key, old.kind, status, old.parent)
        return True

    def children(self, story_key):
        return [t for t in self.tickets.values() if t.parent == story_key]

    def stories_in(self, status):
        return [
            t.key for t in self.tickets.values() if t.kind == rules.STORY and t.status == status
        ]

    def comments(self, key):
        return self._comments.get(key, [])

    def add_comment(self, key, text):
        self.comments_added.append((key, text))

    def notify(self, key, subject, text, account_ids):
        self.notified.append((key, account_ids))

    def entered_status_at(self, key, status):
        return self._entered.get(key)


class FakeGitHub:
    def __init__(self, open_keys=(), deployed=()):
        self.open_keys = set(open_keys)
        self.deployed = list(deployed)
        self.asked = []

    def open_pr_keys(self, repos, skip):
        self.asked.append((tuple(repos), skip))
        return self.open_keys

    def deployed_prs(self, repo, workflow_id, run_id, head_sha):
        return self.deployed


def sub(key, status, parent="SCRUM-79"):
    return Ticket(key, rules.WORK, status, parent)


def story(key="SCRUM-79", status=IN_PROGRESS):
    return Ticket(key, rules.STORY, status, None)


def pr_event(action, branch, *, merged=False, draft=False, title="", base="staging", fork=False):
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": 12,
            "merged": merged,
            "draft": draft,
            "title": title,
            "base": {"ref": base},
            "head": {"ref": branch, "repo": {"full_name": "someone/fork" if fork else REPO}},
        },
    }


BRANCH = "feat/pbi15-SCRUM-79-SCRUM-321-x"


def run_pr(jira, event, gh=None):
    report = sync.Report()
    sync.on_pr(jira, gh or FakeGitHub(), event, REPOS, report)
    return report


# --- pull requests ----------------------------------------------------------


def test_opened_pr_moves_the_subtask_to_in_review_and_never_the_story():
    jira = FakeJira(story(), sub("SCRUM-321", IN_PROGRESS))
    report = run_pr(jira, pr_event("opened", BRANCH))
    assert jira.moves == [("SCRUM-321", IN_REVIEW)]
    assert report.moved == ["SCRUM-321: In Progress → In Review"]


def test_opened_pr_starts_a_story_still_in_todo():
    jira = FakeJira(story(status=TODO), sub("SCRUM-321", TODO))
    run_pr(jira, pr_event("opened", BRANCH))
    assert ("SCRUM-79", IN_PROGRESS) in jira.moves


def test_merge_moves_to_done_and_last_child_sends_story_to_po_review():
    jira = FakeJira(story(), sub("SCRUM-321", IN_REVIEW), sub("SCRUM-320", DONE))
    report = run_pr(jira, pr_event("closed", BRANCH, merged=True), FakeGitHub())
    assert jira.moves == [("SCRUM-321", DONE), ("SCRUM-79", PO_REVIEW)]
    assert jira.notified == []  # no PO account configured in this run
    assert "SCRUM-79: In Progress → PO Review" in report.moved


def test_po_is_notified_when_an_account_is_configured():
    jira = FakeJira(story(), sub("SCRUM-321", IN_REVIEW))
    report = sync.Report()
    sync.on_pr(
        jira, FakeGitHub(), pr_event("closed", BRANCH, merged=True), REPOS, report, po_account="po"
    )
    assert jira.notified == [("SCRUM-79", ["po"])]


def test_merge_waits_while_another_open_pr_names_the_same_subtask():
    jira = FakeJira(story(), sub("SCRUM-321", IN_REVIEW))
    gh = FakeGitHub(open_keys={"SCRUM-321"})
    report = run_pr(jira, pr_event("closed", BRANCH, merged=True), gh)
    assert jira.moves == []
    assert gh.asked == [(tuple(REPOS), (REPO, 12))]
    assert report.notes == ["SCRUM-321: left In Review (another open PR names it)"]


def test_story_waits_while_another_child_is_open():
    jira = FakeJira(story(), sub("SCRUM-321", IN_REVIEW), sub("SCRUM-322", IN_PROGRESS))
    run_pr(jira, pr_event("closed", BRANCH, merged=True))
    assert jira.moves == [("SCRUM-321", DONE)]


def test_closed_without_merge_goes_back_to_in_progress():
    jira = FakeJira(story(), sub("SCRUM-321", IN_REVIEW))
    run_pr(jira, pr_event("closed", BRANCH))
    assert jira.moves == [("SCRUM-321", IN_PROGRESS)]


def test_draft_and_other_actions_move_nothing():
    jira = FakeJira(story(), sub("SCRUM-321", IN_PROGRESS))
    assert run_pr(jira, pr_event("opened", BRANCH, draft=True)).notes == [
        "draft or ignored action: nothing to do"
    ]
    run_pr(jira, pr_event("edited", BRANCH))
    assert jira.moves == []


def test_pr_body_keys_are_never_used():
    # E29: SCRUM-198 was only mentioned in the body; the handler never reads it
    event = pr_event("closed", "fix/pbi23-SCRUM-177-SCRUM-204-x", merged=True)
    event["pull_request"]["body"] = "Related: SCRUM-198"
    jira = FakeJira(
        story("SCRUM-177"),
        sub("SCRUM-204", IN_REVIEW, "SCRUM-177"),
        sub("SCRUM-198", IN_PROGRESS, "SCRUM-177"),
    )
    run_pr(jira, event)
    assert ("SCRUM-198", DONE) not in jira.moves


def test_fork_prs_and_other_bases_are_skipped():
    jira = FakeJira(story(), sub("SCRUM-321", IN_PROGRESS))
    assert run_pr(jira, pr_event("opened", BRANCH, fork=True)).notes == ["PR from a fork: skipped"]
    assert run_pr(jira, pr_event("opened", BRANCH, base="main")).notes == [
        "PR into main, not staging: skipped"
    ]
    assert jira.moves == []


def test_pr_without_keys_is_a_note_not_a_warning():
    report = run_pr(FakeJira(), pr_event("opened", "chore/x"))
    assert report.notes == ["no Jira key in the branch name or title"]
    assert report.warnings == []


def test_missing_ticket_and_jira_errors_are_warnings():
    jira = FakeJira(fail={"SCRUM-321"})
    report = run_pr(jira, pr_event("opened", "feat/pbi15-SCRUM-404-SCRUM-321-x"))
    assert report.warnings == ["SCRUM-404: not found in Jira", "SCRUM-321: Jira error (boom)"]


def test_no_matching_transition_is_a_warning():
    jira = FakeJira(story(), sub("SCRUM-321", IN_PROGRESS))
    jira.move = lambda key, status: False
    report = run_pr(jira, pr_event("opened", BRANCH))
    assert report.warnings == ["SCRUM-321: no transition to In Review"]


def test_story_key_in_the_branch_is_only_a_note():
    report = run_pr(FakeJira(story()), pr_event("opened", "feat/pbi15-SCRUM-79-x"))
    assert report.notes == ["SCRUM-79: not a subtask or task"]


# --- branch created ---------------------------------------------------------


def test_new_branch_starts_the_subtask_and_its_story():
    jira = FakeJira(story(status=TODO), sub("SCRUM-321", TODO))
    report = sync.Report()
    sync.on_branch(jira, {"ref": BRANCH, "ref_type": "branch"}, report)
    assert jira.moves == [("SCRUM-321", IN_PROGRESS), ("SCRUM-79", IN_PROGRESS)]


def test_new_tag_or_branch_without_key_does_nothing():
    jira = FakeJira(sub("SCRUM-321", TODO))
    report = sync.Report()
    sync.on_branch(jira, {"ref": "v1.0-SCRUM-321", "ref_type": "tag"}, report)
    sync.on_branch(jira, {"ref": "chore/x", "ref_type": "branch"}, report)
    assert jira.moves == []


# --- deploy -----------------------------------------------------------------


def deploy_event(conclusion="success"):
    return {
        "repository": {"full_name": REPO},
        "workflow_run": {
            "id": 100,
            "workflow_id": 7,
            "head_sha": "abcdef1234",
            "conclusion": conclusion,
            "html_url": "https://run/100",
        },
    }


def test_deploy_comments_live_on_staging_once_per_ticket():
    already = Comment("bot", "Live on staging: org/server@abcdef1, https://run/99", NOW)
    jira = FakeJira(
        sub("SCRUM-321", DONE), sub("SCRUM-322", DONE), comments={"SCRUM-322": [already]}
    )
    gh = FakeGitHub(deployed=[(BRANCH, ""), ("feat/pbi15-SCRUM-79-SCRUM-322-y", ""), (BRANCH, "")])
    report = sync.Report()
    sync.on_deploy(jira, gh, deploy_event(), report)
    assert jira.comments_added == [
        ("SCRUM-321", "Live on staging: org/server@abcdef1, https://run/100")
    ]
    assert report.moved == ["SCRUM-321: commented Live on staging"]


def test_failed_deploy_comments_nothing():
    jira = FakeJira(sub("SCRUM-321", DONE))
    report = sync.Report()
    sync.on_deploy(jira, FakeGitHub(deployed=[(BRANCH, "")]), deploy_event("failure"), report)
    assert jira.comments_added == []
    assert report.notes == ["deploy did not succeed: nothing to do"]


# --- PO review --------------------------------------------------------------


def test_po_acc_comment_moves_the_story_to_done():
    acc = Comment("po", "ACC, dicek di dev", NOW - timedelta(hours=1))
    jira = FakeJira(
        story(status=PO_REVIEW),
        comments={"SCRUM-79": [acc]},
        entered={"SCRUM-79": NOW - timedelta(hours=3)},
    )
    report = sync.Report()
    sync.on_po_review(jira, "po", NOW, report)
    assert jira.moves == [("SCRUM-79", DONE)]
    assert report.moved == ["SCRUM-79: PO Review → Done (ACC by the PO)"]


def test_po_reminder_after_a_day():
    jira = FakeJira(story(status=PO_REVIEW), entered={"SCRUM-79": NOW - timedelta(hours=25)})
    report = sync.Report()
    sync.on_po_review(jira, "po", NOW, report)
    assert jira.moves == []
    assert jira.notified == [("SCRUM-79", ["po"])]
    assert jira.comments_added[0][1].startswith(rules.REMINDER_MARK)


def test_po_review_without_entry_time_or_po_account_is_a_warning():
    jira = FakeJira(story(status=PO_REVIEW))
    report = sync.Report()
    sync.on_po_review(jira, "po", NOW, report)
    assert report.warnings == ["SCRUM-79: no PO Review entry in the changelog"]
    report = sync.Report()
    sync.on_po_review(jira, "", NOW, report)
    assert report.warnings == ["JIRA_PO_ACCOUNT_ID is not set: PO review check skipped"]


# --- summary ----------------------------------------------------------------


def test_summary_lists_moves_notes_and_warnings():
    report = sync.Report(moved=["SCRUM-1: To Do → In Review"], notes=["n"], warnings=["w"])
    text = report.markdown("pull_request opened #12")
    assert "SCRUM-1: To Do → In Review" in text
    assert "Warnings" in text
    assert sync.Report().markdown("x").endswith("Nothing moved.\n")
