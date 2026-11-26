# Jira status sync (SCRUM-377)

GitHub Actions moves our Jira tickets, so nobody has to drag cards by hand and the burndown stays right.
It replaces the Jira Automation rule Rafa built in Sprint 3, which stopped on 9 Nov 2026 when the Jira Free
quota (100 rule runs a month for the whole site) ran out. This version calls the Jira REST API directly, so it has
no monthly limit, and Actions minutes are free on our public repos.

## What moves, and when

The workflow the team agreed on 29–30 Oct (LINE):

| Ticket | Event | Status |
|---|---|---|
| Subtask or Task named in the branch or PR title | branch created | To Do → In Progress |
| | PR into `staging` opened, reopened or marked ready (not draft) | → In Review |
| | PR merged into `staging` | → Done, but only when no other open PR (either repo) names it |
| | PR closed without merge | In Review → In Progress |
| Story (PBI) | its first ticket starts | To Do → In Progress |
| | every child is Done | In Progress → PO Review, and the PO gets an email |
| | the PO comments starting with **ACC** | PO Review → Done (checked every 30 min) |
| | 24 h in PO Review without ACC | one reminder comment + email, never an automatic Done |
| | client accepts at UAT | Client Accepted, **by hand** (PO) |
| Ticket in a deploy | "Deploy staging" succeeded | one comment `Live on staging: <repo>@<sha>, <run link>` |

Rules that keep it safe:

- Keys come only from the branch name and the PR title, never from the PR body or commits. Mentioning another
  ticket in a PR body moved SCRUM-198 to Done in October; that cannot happen here.
- `SCRUM-27.6` (a test case id) and `XSCRUM-12` do not count as keys.
- Nothing moves backwards, except the agreed "closed without merge" rule. A fix PR for a Done ticket leaves it Done.
- Story keys in a branch name never move the story directly; the story follows its children.
- Statuses are matched by id (To Do 10000, In Progress 10001, In Review 10002, Done 10003, PO Review 10033,
  Client Accepted 10034), not by name.
- A Jira error is a warning on the job, never a red check, so a Jira outage never blocks a PR.
- Every run writes what it moved and why on the run's summary page (Actions → the run → Summary).

## Files

| File | Repo | Trigger |
|---|---|---|
| `.github/workflows/jira-sync.yml` | server | reusable workflow, the only place that runs the script |
| `.github/workflows/jira-pr.yml` | server + client | `pull_request_target` (opened, reopened, ready_for_review, closed) and `create` |
| `.github/workflows/jira-deploy.yml` | server + client | `workflow_run` of "Deploy staging" |
| `.github/workflows/jira-po-review.yml` | server only | every 30 min, and by hand (Run workflow) |
| `scripts/jira_sync/` | server | the logic, Python standard library only; tests in `tests/scripts/test_jira_sync_*.py` |

Security: `pull_request_target` runs the workflow from `staging`, and the job checks out only `scripts/jira_sync`
from server `staging` without credentials. It never runs the PR's code, and PRs from forks are skipped, so a PR
cannot reach the secrets.

## Setup (repo admin, once)

Add these repository secrets in **both** repos (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `JIRA_EMAIL` | the Atlassian account the moves are made as |
| `JIRA_API_TOKEN` | an API token of that account (id.atlassian.com → Security → API tokens) |
| `JIRA_PO_ACCOUNT_ID` | the PO's Jira account id (for the email and the ACC check) |

Then turn the old rule off in Jira (Settings → System → Automation), so the two do not both run when the Jira
quota resets on the 1st.

Until the secrets exist, every run prints a notice and does nothing.

## Check that it works

1. Open a draft PR for a test subtask: nothing moves. Mark it ready: the subtask goes to In Review.
2. Look at the run's Summary page: it lists `SCRUM-…: In Progress → In Review`.
3. Actions → "Jira PO review check" → Run workflow: stories in PO Review are checked right away.

## Run the tests

```
pytest tests/scripts -o addopts="" --cov=scripts/jira_sync --cov-branch
```
