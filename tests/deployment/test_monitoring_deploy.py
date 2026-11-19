"""Monitoring sampai ke VM: env Sentry di compose, dan deploy gagal bila API tidak sehat."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")


def _render_api_environment(tmp_path, overrides):
    empty = tmp_path / "empty-compose-input.txt"
    empty.write_text("")
    # Sengaja minimal: jangan pernah mewarisi kredensial deploy yang asli.
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
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "-f", str(ROOT / "deploy/compose.yml")]
        + ["config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["services"]["api"]["environment"]


def test_sentry_is_off_on_the_vm_until_a_dsn_is_set(tmp_path):
    values = _render_api_environment(tmp_path, {})
    assert values["SENTRY_DSN"] == ""
    assert values["SENTRY_RELEASE"] == ""


def test_dsn_and_release_reach_the_api(tmp_path):
    values = _render_api_environment(
        tmp_path,
        {
            "SENTRY_DSN": "https://public@sentry.invalid/1",
            "SENTRY_RELEASE": "indolegalbench-server@abc123",
        },
    )
    assert values["SENTRY_DSN"] == "https://public@sentry.invalid/1"
    assert values["SENTRY_RELEASE"] == "indolegalbench-server@abc123"


# --- wait_until_healthy di deploy.sh, dengan `docker` palsu ---------------------

_FAKE_DOCKER = """#!/usr/bin/env bash
# Mengembalikan status health berikutnya dari daftar, satu per panggilan inspect.
case "$1 $2" in
  "compose ps") [ -s "$FAKE_DIR/no-container" ] || echo fake-api-id ;;
  "compose logs") echo "log terakhir api" ;;
  inspect*)
    [ -s "$FAKE_DIR/no-container" ] && exit 1
    head -n 1 "$FAKE_DIR/statuses"
    if [ "$(wc -l < "$FAKE_DIR/statuses")" -gt 1 ]; then
      tail -n +2 "$FAKE_DIR/statuses" > "$FAKE_DIR/rest" && mv "$FAKE_DIR/rest" "$FAKE_DIR/statuses"
    fi ;;
esac
"""


def _wait(tmp_path, statuses, seconds=30, no_container=False):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(_FAKE_DOCKER, newline="\n")
    docker.chmod(0o755)
    (tmp_path / "statuses").write_text("\n".join(statuses) + "\n", newline="\n")
    (tmp_path / "no-container").write_text("x" if no_container else "")
    # Ambil fungsi dari deploy.sh tanpa menjalankan main (baris terakhir).
    script = (ROOT / "deploy/deploy.sh").read_text().replace('main "$@"; exit', "")
    runner = tmp_path / "run.sh"
    runner.write_text(
        script + f"\nsleep() {{ :; }}\nwait_until_healthy api {seconds}\n", newline="\n"
    )
    return subprocess.run(
        [BASH, str(runner)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )


needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")


@needs_bash
def test_deploy_passes_once_the_new_api_turns_healthy(tmp_path):
    """Edge: container baru selalu mulai sebagai `starting`; pemeriksaan pertama baru
    jalan setelah interval healthcheck (30 detik). Kalau `starting` dianggap gagal,
    setiap deploy yang sehat ikut merah."""
    result = _wait(tmp_path, ["starting", "starting", "healthy"])
    assert result.returncode == 0, result.stderr
    assert "api is healthy" in result.stdout


@needs_bash
def test_deploy_fails_and_shows_logs_when_the_api_is_unhealthy(tmp_path):
    result = _wait(tmp_path, ["starting", "unhealthy"])
    assert result.returncode == 1
    assert "status: unhealthy" in result.stderr
    assert "log terakhir api" in result.stderr


@needs_bash
def test_deploy_fails_when_the_api_never_finishes_starting(tmp_path):
    """Edge: API yang macet saat start (misalnya menunggu DB) tetap `starting` dan tidak
    pernah `unhealthy`. Tanpa batas waktu, job CD menunggu selamanya dan kunci deploy
    tidak pernah lepas, jadi merge berikutnya juga tidak ter-deploy."""
    result = _wait(tmp_path, ["starting"], seconds=15)
    assert result.returncode == 1
    assert "status: starting" in result.stderr


@needs_bash
def test_deploy_fails_when_the_api_container_is_missing(tmp_path):
    """Edge: bila `up -d` gagal membuat container, `ps -q` kosong dan inspect gagal.
    Tanpa cabang ini, string kosong bisa terbaca sebagai status yang tidak dikenal
    dan loop menunggu sampai habis tanpa pesan yang jelas."""
    result = _wait(tmp_path, ["healthy"], no_container=True)
    assert result.returncode == 1
    assert "status: missing" in result.stderr
