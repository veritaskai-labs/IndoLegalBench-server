"""Smoke test: setiap app/modules/*/models.py bisa diimpor.

migrations/env.py mengimpor models dari tiap modul supaya tabelnya
terdaftar di Base.metadata. Kalau satu models.py rusak, misalnya karena
impor melingkar, Alembic gagal total. Test ini menangkapnya lebih dulu,
termasuk untuk modul yang models.py-nya masih kosong.
"""

import importlib
from pathlib import Path

import pytest

import app.modules

MODUL_DENGAN_MODELS = sorted(
    path.parent.name for path in Path(app.modules.__file__).parent.glob("*/models.py")
)


def test_daftar_modul_ditemukan():
    assert "auth" in MODUL_DENGAN_MODELS
    assert "suites" in MODUL_DENGAN_MODELS


@pytest.mark.parametrize("modul", MODUL_DENGAN_MODELS)
def test_models_bisa_diimpor(modul: str):
    importlib.import_module(f"app.modules.{modul}.models")
