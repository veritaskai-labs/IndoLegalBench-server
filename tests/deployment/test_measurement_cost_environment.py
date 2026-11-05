"""SCRUM-250: render Compose with synthetic inputs, then parse the process settings.

No daemon, deployment, credential file or external provider is used.
"""

import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.shared.config import Settings

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose.yml"
COST_ENV = {
    "COST_MODEL_PRICES": '{"openai_compatible:synthetic-model":"2.5"}',
    "COST_MODEL_INPUT_PRICES": '{"openai_compatible:synthetic-model":"0.5"}',
    "COST_SAFETY_MARGIN_PERCENT": "25",
    "COST_USD_IDR_FALLBACK_RATE": "16000.5",
    "COST_DEFAULT_AVG_ANSWER_TOKENS": "750.5",
    "JISDOR_API_URL": "https://rates.example.test/latest",
    "JISDOR_API_KEY": "synthetic-rate-fixture",  # pragma: allowlist secret
}
pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker Compose CLI required"
)


def _render(values, settings_file=None):
    # Isolate from host secrets and deploy/.env; Compose config never starts a container.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "STACK": "dev-test",
        "APP_ENV": "staging",
        "POSTGRES_PASSWORD": "synthetic-db-fixture",  # pragma: allowlist secret
        "POSTGRES_VOLUME": "scrum250-test-only",
        "WEB_DOMAIN": "web.example.test",
        "API_DOMAIN": "api.example.test",
        "ZITADEL_ISSUER": "https://auth.example.test",
        "ZITADEL_CLIENT_ID": "synthetic-client",
        **values,
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(settings_file or "/dev/null"),
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["services"]


@pytest.mark.parametrize("service", ["api", "worker"])
@pytest.mark.parametrize("source", ["host", "file"])
def test_configured_cost_values_reach_each_process_and_parse_as_settings(
    monkeypatch, tmp_path, service, source
):
    if source == "file":
        settings_file = tmp_path / "synthetic-config.txt"
        settings_file.write_text("\n".join(f"{key}='{value}'" for key, value in COST_ENV.items()))
        services = _render({}, settings_file)
    else:
        services = _render(COST_ENV)
    environment = services[service]["environment"]
    for name, value in COST_ENV.items():
        assert environment.get(name) == value, f"{service} lost {name}"
        monkeypatch.setenv(name, environment[name])
    settings = Settings(_env_file=None)
    assert settings.cost_model_prices == {"openai_compatible:synthetic-model": Decimal("2.5")}
    assert settings.cost_model_input_prices == {"openai_compatible:synthetic-model": Decimal("0.5")}
    assert settings.cost_safety_margin_percent == Decimal("25")
    assert settings.cost_usd_idr_fallback_rate == Decimal("16000.5")
    assert settings.cost_default_avg_answer_tokens == Decimal("750.5")
    assert settings.jisdor_api_url == COST_ENV["JISDOR_API_URL"]
    assert settings.jisdor_api_key == COST_ENV["JISDOR_API_KEY"]


@pytest.mark.parametrize("service", ["api", "worker"])
def test_absent_optional_values_preserve_settings_defaults_without_blank_decimals(
    monkeypatch, service
):
    environment = _render({})[service]["environment"]
    for name in COST_ENV:
        monkeypatch.delenv(name, raising=False)
        value = environment.get(name)
        if value is not None:
            monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.cost_model_prices == {}
    assert settings.cost_model_input_prices == {}
    assert settings.cost_safety_margin_percent == Decimal("20")
    assert settings.cost_usd_idr_fallback_rate is None
    assert settings.cost_default_avg_answer_tokens is None
    assert settings.jisdor_api_url == "https://allratestoday.com/api/v1/central-bank/bi/latest"
    assert settings.jisdor_api_key == ""


@pytest.mark.parametrize("service", ["api", "worker"])
def test_approved_initial_estimate_reaches_each_process_without_static_rate(monkeypatch, service):
    values = {**COST_ENV, "COST_DEFAULT_AVG_ANSWER_TOKENS": "1000"}
    del values["COST_USD_IDR_FALLBACK_RATE"]
    environment = _render(values)[service]["environment"]
    for name in COST_ENV:
        monkeypatch.delenv(name, raising=False)
        if environment.get(name) is not None:
            monkeypatch.setenv(name, environment[name])
    settings = Settings(_env_file=None)
    assert settings.cost_default_avg_answer_tokens == Decimal("1000")
    assert settings.cost_usd_idr_fallback_rate is None
    assert settings.jisdor_api_key == COST_ENV["JISDOR_API_KEY"]
