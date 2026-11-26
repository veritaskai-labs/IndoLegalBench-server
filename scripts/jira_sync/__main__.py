"""Entry point for the workflows: python -m scripts.jira_sync <pr|branch|deploy|po-review>.

Reads the GitHub event from GITHUB_EVENT_PATH and the Jira credentials from the
environment (repository secrets). Always exits 0 after a run: a Jira problem is a
warning on the job, never a red check that blocks a PR.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.jira_sync import sync
from scripts.jira_sync.clients import GitHubClient, JiraClient

COMMANDS = ("pr", "branch", "deploy", "po-review")


def _now() -> datetime:
    return datetime.now(UTC)


def _run(command: str, env: dict, report: sync.Report) -> str:
    event = (
        json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if command != "po-review"
        else {}
    )
    jira = JiraClient(env["JIRA_BASE_URL"], env["JIRA_EMAIL"], env["JIRA_API_TOKEN"])
    github = GitHubClient(env.get("GITHUB_TOKEN", ""))
    repos = [r.strip() for r in env.get("JIRA_SYNC_REPOS", "").split(",") if r.strip()]
    po_account = env.get("JIRA_PO_ACCOUNT_ID", "")
    if command == "pr":
        sync.on_pr(jira, github, event, repos, report, po_account=po_account)
        return f"pull request {event.get('action')} #{event['pull_request']['number']}"
    if command == "branch":
        sync.on_branch(jira, event, report)
        return f"branch {event.get('ref')}"
    if command == "deploy":
        sync.on_deploy(jira, github, event, report)
        return "deploy"
    sync.on_po_review(jira, po_account, _now(), report)
    return "PO review check"


def main(argv: list[str], env: dict) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(f"usage: python -m scripts.jira_sync <{'|'.join(COMMANDS)}>")
        return 2
    missing = [
        name for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not env.get(name)
    ]
    if missing:
        print(f"::notice::Jira sync skipped: {', '.join(missing)} not set.")
        return 0
    report = sync.Report()
    title = argv[0]
    try:
        title = _run(argv[0], env, report)
    except Exception as error:  # noqa: BLE001 - one bad run must never block a PR
        report.warnings.append(f"Jira sync failed: {error}")
    for warning in report.warnings:
        print(f"::warning::{warning}")
    summary = report.markdown(title)
    if env.get("GITHUB_STEP_SUMMARY"):
        with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - thin shell entry
    sys.exit(main(sys.argv[1:], dict(os.environ)))
