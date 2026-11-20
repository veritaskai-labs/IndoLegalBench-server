"""Skrip dasbor lokal menolak host cloud dan tidak membuka jaringan."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "langfuse_local_scores.py"


def _script():
    spec = importlib.util.spec_from_file_location("langfuse_local_scores", _PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_host_kosong_dan_cloud_ditolak_tanpa_jaringan(monkeypatch):
    """Negatif: URL kosong atau *.langfuse.com keluar sebelum ada request."""
    script = _script()

    def refuse(*args, **kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(script.urllib.request, "urlopen", refuse)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "")
    assert script.main() == 2

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    assert script.main() == 2
    assert script.cloud_host("https://jp.cloud.langfuse.com") is True
    assert script.cloud_host("http://127.0.0.1:3000") is False


def test_ubin_memakai_rute_dan_ukuran_yang_dikonfirmasi():
    """Positif: dasbor memakai view dan measure dari API Langfuse saat ini."""
    script = _script()
    names = {item["name"]: item for item in script.widgets()}
    assert names["Tingkat berhasil"]["view"] == "scores-boolean"
    assert names["Tingkat berhasil"]["metrics"] == [{"measure": "value", "agg": "avg"}]
    assert names["Campuran kegagalan"]["view"] == "scores-categorical"
    assert names["Latensi p95"]["metrics"] == [{"measure": "latency", "agg": "p95"}]
    assert names["Volume percobaan"]["metrics"] == [{"measure": "count", "agg": "count"}]
    assert names["Biaya USD"]["metrics"] == [{"measure": "totalCost", "agg": "sum"}]
    assert script.DASHBOARD_NAME == "Pengukuran eksekusi"
