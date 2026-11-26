"""Rules of the Jira status sync (SCRUM-377), pure functions only.

The agreed workflow (LINE 29-30 Oct) and the replay on the last 60 PRs are in the
PR body. No network here: every input is a plain value.
"""

from datetime import UTC, datetime, timedelta

import pytest

from scripts.jira_sync import rules
from scripts.jira_sync.rules import (
    DONE,
    IN_PROGRESS,
    IN_REVIEW,
    PO_REVIEW,
    TODO,
    Comment,
    Ticket,
)

PO = "po-account"
ENTERED = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


def work(key="SCRUM-321", status=TODO, parent="SCRUM-79"):
    return Ticket(key=key, kind=rules.WORK, status=status, parent=parent)


def story(key="SCRUM-79", status=IN_PROGRESS):
    return Ticket(key=key, kind=rules.STORY, status=status, parent=None)


# --- keys -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("branch", "title", "expected"),
    [
        # the usual subtask branch: the story key comes first
        (
            "feat/pbi15-SCRUM-79-SCRUM-321-report-builder-page",
            "feat(reports): SCRUM-79 SCRUM-321 report builder page",
            ["SCRUM-79", "SCRUM-321"],
        ),
        # task branch with one key (SCRUM-174 precedent)
        ("ci/SCRUM-174-sonarqube-pr-gate", "ci(sonar): run the gate", ["SCRUM-174"]),
        # two tickets in one branch (server #92)
        (
            "fix/pbi23-SCRUM-177-SCRUM-245-SCRUM-246-migration-report",
            "",
            ["SCRUM-177", "SCRUM-245", "SCRUM-246"],
        ),
        # lowercase branch from an agent (client #98), title has the key too
        ("cursor/scrum-299-laporan-cost-columns-29ba", "Laporan SCRUM-299", ["SCRUM-299"]),
        # key only in the title
        ("chore/fix-typo", "fix(ui): SCRUM-368 typo", ["SCRUM-368"]),
        # no key at all
        ("chore/vitest-vmthreads-pool", "ci(test): run Vitest in vmThreads", []),
    ],
)
def test_keys_come_from_branch_then_title(branch, title, expected):
    assert rules.keys_from_pr(branch, title) == expected


def test_a_key_with_a_dot_or_more_digits_does_not_count():
    assert rules.keys_from_pr("feat/SCRUM-27.6-x", "SCRUM-1234x SCRUM-12a") == []


def test_a_key_glued_to_other_letters_does_not_count():
    assert rules.keys_from_pr("feat/XSCRUM-12-x", "") == []


def test_other_projects_do_not_count():
    assert rules.keys_from_pr("feat/ABC-12-x", "PBI-12: login") == []


# --- PR event -> target -----------------------------------------------------


@pytest.mark.parametrize(
    ("action", "merged", "draft", "expected"),
    [
        ("opened", False, False, IN_REVIEW),
        ("reopened", False, False, IN_REVIEW),
        ("ready_for_review", False, False, IN_REVIEW),
        ("opened", False, True, None),  # draft moves nothing
        ("closed", True, False, DONE),
        ("closed", False, False, IN_PROGRESS),  # closed without merge
        ("edited", False, False, None),
        ("synchronize", False, False, None),
    ],
)
def test_pr_event_target(action, merged, draft, expected):
    assert rules.target_for_pr(action, merged=merged, draft=draft) == expected


# --- decide a move for one ticket -------------------------------------------


def test_opened_pr_moves_todo_or_in_progress_to_in_review():
    assert rules.decide(work(status=TODO), IN_REVIEW, other_open_pr=False).to == IN_REVIEW
    assert rules.decide(work(status=IN_PROGRESS), IN_REVIEW, other_open_pr=False).to == IN_REVIEW


def test_merge_moves_to_done():
    assert rules.decide(work(status=IN_REVIEW), DONE, other_open_pr=False).to == DONE


def test_merge_waits_for_the_last_open_pr_of_the_same_ticket():
    decision = rules.decide(work(status=IN_REVIEW), DONE, other_open_pr=True)
    assert decision.to is None
    assert "another open PR" in decision.reason


def test_never_moves_backwards():
    # a follow-up fix PR for a ticket that is already Done
    decision = rules.decide(work(status=DONE), IN_REVIEW, other_open_pr=False)
    assert decision.to is None
    assert decision.reason == "already Done"


def test_same_status_is_not_a_move():
    assert rules.decide(work(status=IN_REVIEW), IN_REVIEW, other_open_pr=False).to is None


def test_closed_without_merge_goes_back_to_in_progress_from_in_review_only():
    assert rules.decide(work(status=IN_REVIEW), IN_PROGRESS, other_open_pr=False).to == IN_PROGRESS
    # a Done ticket is never reopened by closing a later PR
    assert rules.decide(work(status=DONE), IN_PROGRESS, other_open_pr=False).to is None
    # another PR for the same ticket is still under review
    assert rules.decide(work(status=IN_REVIEW), IN_PROGRESS, other_open_pr=True).to is None


def test_branch_created_moves_todo_to_in_progress():
    assert rules.decide(work(status=TODO), IN_PROGRESS, other_open_pr=False).to == IN_PROGRESS
    assert (
        rules.decide(work(status=IN_REVIEW), IN_PROGRESS, other_open_pr=False, closing=False).to
        is None
    )


def test_stories_and_unknown_types_are_never_moved_by_a_pr():
    assert rules.decide(story(), DONE, other_open_pr=False).reason == "not a subtask or task"
    other = Ticket(key="SCRUM-1", kind=rules.OTHER, status=TODO, parent=None)
    assert rules.decide(other, IN_REVIEW, other_open_pr=False).to is None


def test_unknown_status_is_left_alone():
    decision = rules.decide(work(status="99999"), DONE, other_open_pr=False)
    assert decision.to is None
    assert "unknown status" in decision.reason


# --- story follows its children ---------------------------------------------


def test_story_starts_with_its_first_ticket():
    assert rules.story_on_start(story(status=TODO)) == IN_PROGRESS
    assert rules.story_on_start(story(status=IN_PROGRESS)) is None
    assert rules.story_on_start(story(status=PO_REVIEW)) is None


def test_story_goes_to_po_review_when_every_child_is_done():
    children = [work("SCRUM-1", DONE), work("SCRUM-2", DONE)]
    assert rules.story_after_done(story(), children) == PO_REVIEW


def test_story_waits_while_a_child_is_open():
    children = [work("SCRUM-1", DONE), work("SCRUM-2", IN_REVIEW)]
    assert rules.story_after_done(story(), children) is None


def test_story_without_children_or_past_in_progress_is_left_alone():
    assert rules.story_after_done(story(), []) is None
    assert rules.story_after_done(story(status=DONE), [work("SCRUM-1", DONE)]) is None


# --- PO review: "ACC" comment -> Done, reminder after 24 h ------------------


def comment(body, author=PO, hours=1):
    return Comment(author=author, body=body, created=ENTERED + timedelta(hours=hours))


def test_acc_comment_by_the_po_moves_the_story_to_done():
    now = ENTERED + timedelta(hours=2)
    assert rules.po_review_action(ENTERED, [comment("ACC, AC dicek di dev")], PO, now) == "done"
    assert rules.po_review_action(ENTERED, [comment("  acc ok")], PO, now) == "done"


def test_acc_by_someone_else_or_before_po_review_does_not_count():
    now = ENTERED + timedelta(hours=2)
    assert rules.po_review_action(ENTERED, [comment("ACC", author="dev")], PO, now) is None
    assert rules.po_review_action(ENTERED, [comment("ACC", hours=-1)], PO, now) is None


@pytest.mark.parametrize(
    "body",
    [
        "ACC",
        "Acc, mantap",
        "ACC ✅",
        "ACC.",
        "ACC!",
        "ACC: sudah oke",
        "ACC\nSudah dicek di dev",
        " acc ok",
    ],
)
def test_acc_with_punctuation_or_a_new_line_counts(body):
    now = ENTERED + timedelta(hours=2)
    assert rules.po_review_action(ENTERED, [comment(body)], PO, now) == "done"


@pytest.mark.parametrize("body", ["Belum ACC ya", "accordingly fixed", "ACC_x", "accepted?"])
def test_words_that_only_contain_acc_do_not_count(body):
    now = ENTERED + timedelta(hours=2)
    assert rules.po_review_action(ENTERED, [comment(body)], PO, now) is None


def test_a_question_from_the_po_is_not_an_acceptance():
    now = ENTERED + timedelta(hours=2)
    assert rules.po_review_action(ENTERED, [comment("Accordion kok gak kebuka?")], PO, now) is None


def test_reminder_after_24_hours_only_once_and_never_done():
    comments = [comment("Kenapa tombolnya biru?")]
    assert rules.po_review_action(ENTERED, comments, PO, ENTERED + timedelta(hours=23)) is None
    assert rules.po_review_action(ENTERED, comments, PO, ENTERED + timedelta(hours=24)) == "remind"
    reminded = [*comments, comment(rules.REMINDER_MARK + " please check", author="bot", hours=25)]
    assert rules.po_review_action(ENTERED, reminded, PO, ENTERED + timedelta(hours=30)) is None


def test_status_names_for_the_summary():
    assert rules.status_name(IN_REVIEW) == "In Review"
    assert rules.status_name("42") == "status 42"
