"""SCRUM-75 SCRUM-133: soft delete and name reuse for AI products.

Admin can delete a product (DELETE /admin/providers/{id}). Deleted products
are invisible everywhere and their names can be reused for new products.
"""

import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB
from app.modules.providers.models import AiProduct
from app.shared.config import get_settings
from tests.login import complete_login


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _payload(**override) -> dict:
    body = {
        "name": "AiYU",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1/chat/completions",
        "model_name": "aiyu-1",
        "credential": "test-secret-key",  # pragma: allowlist secret
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    body.update(override)
    return body


# --- soft delete endpoint ---


def test_admin_can_soft_delete_product(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    assert created.status_code == 201
    product_id = created.json()["id"]

    # Act
    response = client.delete(f"/admin/providers/{product_id}")

    # Assert: 204 No Content
    assert response.status_code == 204
    assert response.content == b""

    # deleted_at diisi di DB
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert stored is not None  # baris masih ada di DB
    assert stored.deleted_at is not None


def test_delete_nonexistent_product_returns_404(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)

    # Act
    response = client.delete(f"/admin/providers/{uuid.uuid4()}")

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_delete_already_deleted_product_returns_404(client, db_session, encryption_key):
    # Arrange: hapus dua kali
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="ToDeleteTwice"))
    product_id = created.json()["id"]
    client.delete(f"/admin/providers/{product_id}")

    # Act: hapus lagi
    response = client.delete(f"/admin/providers/{product_id}")

    # Assert: sudah tidak ada
    assert response.status_code == 404


def test_author_cannot_delete_product(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="AuthorTarget"))
    product_id = created.json()["id"]

    complete_login(client, db_session, AUTHOR_SUB)

    # Act
    response = client.delete(f"/admin/providers/{product_id}")

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


# --- deleted product visibility ---


def test_deleted_product_hidden_from_list(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    client.post("/admin/providers", json=_payload(name="Visible"))
    hidden = client.post("/admin/providers", json=_payload(name="ToHide"))
    hidden_id = hidden.json()["id"]
    client.delete(f"/admin/providers/{hidden_id}")

    # Act
    response = client.get("/admin/providers")

    # Assert: hanya "Visible" yang ada
    names = [p["name"] for p in response.json()]
    assert "Visible" in names
    assert "ToHide" not in names


def test_deleted_product_hidden_from_inactive_list(client, db_session, encryption_key):
    # Arrange: nonaktifkan lalu hapus produk
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="InactiveDeleted"))
    product_id = created.json()["id"]
    client.post(f"/admin/providers/{product_id}/deactivate")
    client.delete(f"/admin/providers/{product_id}")

    # Act: filter produk nonaktif
    response = client.get("/admin/providers", params={"is_active": "false"})

    # Assert: produk terhapus tidak muncul di tab Nonaktif sekalipun
    names = [p["name"] for p in response.json()]
    assert "InactiveDeleted" not in names


def test_deleted_product_get_returns_404(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="GetDeleted"))
    product_id = created.json()["id"]
    client.delete(f"/admin/providers/{product_id}")

    # Act
    response = client.get(f"/admin/providers/{product_id}")

    # Assert
    assert response.status_code == 404


def test_deleted_product_cannot_be_updated(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="UpdateDeleted"))
    product_id = created.json()["id"]
    client.delete(f"/admin/providers/{product_id}")

    # Act
    response = client.patch(
        f"/admin/providers/{product_id}", json={"model_name": "new-model"}
    )

    # Assert
    assert response.status_code == 404


def test_deleted_product_cannot_be_connection_tested(client, db_session, encryption_key):
    # Arrange
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload(name="TestDeleted"))
    product_id = created.json()["id"]
    client.delete(f"/admin/providers/{product_id}")

    # Act
    response = client.post(f"/admin/providers/{product_id}/test-connection")

    # Assert
    assert response.status_code == 404


# --- name reuse ---


def test_deleted_name_can_be_reused_via_endpoint(client, db_session, encryption_key):
    # Arrange: hapus produk pertama
    complete_login(client, db_session, ADMIN_SUB)
    first = client.post("/admin/providers", json=_payload(name="ReusedName"))
    assert first.status_code == 201
    client.delete(f"/admin/providers/{first.json()['id']}")

    # Act: buat produk baru dengan nama sama
    second = client.post("/admin/providers", json=_payload(name="ReusedName"))

    # Assert: berhasil, bukan 409
    assert second.status_code == 201
    assert second.json()["name"] == "ReusedName"
    assert second.json()["id"] != first.json()["id"]


def test_active_duplicate_name_still_rejected(client, db_session, encryption_key):
    # Arrange: produk aktif dengan nama yang sama masih ditolak
    complete_login(client, db_session, ADMIN_SUB)
    client.post("/admin/providers", json=_payload(name="TakenName"))

    # Act
    response = client.post("/admin/providers", json=_payload(name="TakenName"))

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "AI_PRODUCT_NAME_TAKEN"
