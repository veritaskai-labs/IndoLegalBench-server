"""Command line entry of the Jira status sync (SCRUM-377).

Isolation: the handlers in sync are replaced with monkeypatch, so these tests
only check the wiring: which handler runs for which command, how secrets are
read, what goes to the job summary, and that the run never fails a PR.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.jira_sync import __main__ as cli
from scripts.jira_sync import sync

SECRET = "super-secret-token"  # pragma: allowlist secret


@pytest.fixture
def env(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "opened", "pull_request": {"number": 3}}))
    return {
        "JIRA_BASE_URL": "https://example.atlassian.net",
        "JIRA_EMAIL": "bot@example.test",
        "JIRA_API_TOKEN": SECRET,
        "GITHUB_TOKEN": "gh",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "JIRA_SYNC_REPOS": "org/server, org/client",
        "JIRA_PO_ACCOUNT_ID": "po",
    }


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(name):
        def handler(*args, **kwargs):
            seen.append((name, args, kwargs))
            args[-1].moved.append("SCRUM-1: To Do → In Review") if name == "pr" else None

        return handler

    for name in ("pr", "branch", "deploy", "po_review"):
        monkeypatch.setattr(sync, f"on_{name}", fake(name))
    return seen


def test_pr_command_runs_the_pr_handler_and_writes_the_summary(env, calls, capsys):
    assert cli.main(["pr"], env) == 0
    name, args, kwargs = calls[0]
    assert name == "pr"
    assert args[2] == {"action": "opened", "pull_request": {"number": 3}}
    assert args[3] == ["org/server", "org/client"]
    assert kwargs == {"po_account": "po"}
    summary = Path(env["GITHUB_STEP_SUMMARY"]).read_text()
    assert "SCRUM-1: To Do → In Review" in summary
    assert SECRET not in summary + capsys.readouterr().out


@pytest.mark.parametrize("command", ["branch", "deploy"])
def test_branch_and_deploy_commands(env, calls, command):
    assert cli.main([command], env) == 0
    assert calls[0][0] == command


def test_po_review_command_passes_the_po_account_and_the_time(env, calls, monkeypatch):
    now = datetime(2026, 11, 26, tzinfo=UTC)
    monkeypatch.setattr(cli, "_now", lambda: now)
    assert cli.main(["po-review"], env) == 0
    name, args, _ = calls[0]
    assert (name, args[1], args[2]) == ("po_review", "po", now)


def test_missing_secrets_skip_without_failing(env, calls, capsys):
    del env["JIRA_API_TOKEN"]
    assert cli.main(["pr"], env) == 0
    assert calls == []
    assert "::notice::" in capsys.readouterr().out


def test_unknown_command_is_an_error():
    assert cli.main(["nope"], {}) == 2


def test_an_unexpected_error_is_a_warning_not_a_red_check(env, monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise RuntimeError("Jira is down")

    monkeypatch.setattr(sync, "on_pr", explode)
    assert cli.main(["pr"], env) == 0
    out = capsys.readouterr().out
    assert "::warning::Jira sync failed: Jira is down" in out


def test_warnings_are_printed_as_annotations(env, monkeypatch, capsys):
    monkeypatch.setattr(
        sync, "on_pr", lambda *a, **k: a[-1].warnings.append("SCRUM-9: not found in Jira")
    )
    cli.main(["pr"], env)
    assert "::warning::SCRUM-9: not found in Jira" in capsys.readouterr().out


def test_summary_without_step_summary_file_goes_to_stdout(env, calls, capsys):
    del env["GITHUB_STEP_SUMMARY"]
    cli.main(["pr"], env)
    assert "SCRUM-1: To Do → In Review" in capsys.readouterr().out


def test_now_is_timezone_aware():
    assert cli._now().tzinfo is not None
