"""Resume policy configuration reaches the API in local and deployed Compose."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.shared.config import Settings


@pytest.mark.parametrize("compose", ["docker-compose.yml", "deploy/compose.yml"])
@pytest.mark.parametrize(
    "overrides,days,enabled",
    [
        ({}, "7", "false"),
        (
            {"RESUME_LONG_STOP_DAYS": "3", "MEASUREMENT_DEV_AI_CONTROLS_ENABLED": "true"},
            "3",
            "true",
        ),
    ],
)
def test_rendered_api_has_defaults_and_operator_overrides(
    tmp_path, compose, overrides, days, enabled
):
    empty = tmp_path / "empty-compose-input.txt"
    empty.write_text("")
    # Deliberately minimal: never inherit real deployment credentials/settings.
    env = {
        "PATH": os.environ["PATH"],
        "STACK": "dev",
        "APP_ENV": "staging",
        "POSTGRES_PASSWORD": "synthetic-compose-test",  # pragma: allowlist secret
        "POSTGRES_VOLUME": "disposable-test-volume",
        "WEB_DOMAIN": "web.test.invalid",
        "API_DOMAIN": "api.test.invalid",
        "ZITADEL_ISSUER": "https://id.test.invalid",
        "ZITADEL_CLIENT_ID": "test-client",
        **overrides,
    }
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(empty),
            "-f",
            str(root / compose),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    values = json.loads(result.stdout)["services"]["api"]["environment"]
    assert values["RESUME_LONG_STOP_DAYS"] == days
    assert values["MEASUREMENT_DEV_AI_CONTROLS_ENABLED"] == enabled


def test_settings_read_approved_environment_names(monkeypatch):
    monkeypatch.setenv("RESUME_LONG_STOP_DAYS", "2")
    monkeypatch.setenv("MEASUREMENT_DEV_AI_CONTROLS_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.resume_long_stop_days == 2
    assert settings.measurement_dev_ai_controls_enabled is True
