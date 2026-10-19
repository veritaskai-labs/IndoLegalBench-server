"""Nama produk AI untuk kolom Objek audit log (F-06, TC-31, PBI-18 AC4)."""

import uuid

import pytest
from cryptography.fernet import Fernet

from app.modules.providers import service
from app.shared.config import get_settings
from app.shared.security import Role


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _produk(client, nama: str) -> uuid.UUID:
    response = client.post(
        "/admin/providers",
        json={
            "name": nama,
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.test/v1/chat/completions",
            "model_name": "aiyu-1",
            "credential": "test-secret-key",  # pragma: allowlist secret
            "rate_limit_per_minute": 30,
            "monthly_budget_idr": "1500000.00",
        },
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def test_nama_produk_per_id(as_role, db_session, encryption_key):
    client = as_role(Role.ADMIN)
    aiyu, lain = _produk(client, "AiYU"), _produk(client, "Lain")

    assert service.product_names(db_session, {aiyu, lain}) == {aiyu: "AiYU", lain: "Lain"}


def test_produk_yang_sudah_dihapus_tetap_bernama(as_role, db_session, encryption_key):
    """Catatan ai_product.deleted tetap harus terbaca setelah produknya dihapus."""
    client = as_role(Role.ADMIN)
    produk = _produk(client, "AiYU")
    assert client.delete(f"/admin/providers/{produk}").status_code == 204

    assert service.product_names(db_session, {produk}) == {produk: "AiYU"}


def test_id_tak_dikenal_tidak_ikut(db_session):
    assert service.product_names(db_session, {uuid.uuid4()}) == {}


def test_tanpa_id_tanpa_query(db_session):
    assert service.product_names(db_session, set()) == {}
