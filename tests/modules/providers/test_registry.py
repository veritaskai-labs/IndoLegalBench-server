"""SCRUM-75 SCRUM-115: admin registry endpoints.

These hit the real require_roles guard and SQLite, the same way the admin
member flow does. Credentials are write-only: the body may send one, the
JSON may not contain it.
"""

import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.modules.auth.models import User
from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB
from app.modules.providers.crypto import decrypt_credential
from app.modules.providers.models import AiProduct
from app.shared.config import get_settings
from tests.login import complete_login

_SECRET = "provider-token-value-7kPq"  # pragma: allowlist secret
_OTHER_SECRET = "replacement-token-value-9mRx"  # pragma: allowlist secret


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _admin(db_session) -> User:
    return db_session.query(User).filter(User.zitadel_sub == ADMIN_SUB).one()


def _payload(**override) -> dict:
    body = {
        "name": "AiYU",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "aiyu-1",
        "credential": _SECRET,
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    body.update(override)
    return body


def _assert_write_only(body: dict, *secrets: str) -> None:
    assert "credential" not in body
    assert "credential_encrypted" not in body
    assert body["has_credential"] is True
    dumped = str(body)
    for secret in secrets:
        assert secret not in dumped


def test_create_hides_credential_and_starts_active(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.post("/admin/providers", json=_payload())

    assert response.status_code == 201
    body = response.json()
    _assert_write_only(body, _SECRET)
    assert body["credential_hint"] == _SECRET[-4:]
    assert body["name"] == "AiYU"
    assert body["provider_type"] == "openai_compatible"
    assert body["base_url"] == "https://api.example.test/v1"
    assert body["model_name"] == "aiyu-1"
    assert body["rate_limit_per_minute"] == 30
    assert Decimal(str(body["monthly_budget_idr"])) == Decimal("1500000.00")
    assert body["is_active"] is True
    assert body["last_test_at"] is None
    assert body["last_test_status"] is None
    assert body["last_test_message"] is None
    assert body["created_by"] == str(_admin(db_session).id)

    db_session.expire_all()
    stored = db_session.query(AiProduct).filter(AiProduct.name == "AiYU").one()
    assert decrypt_credential(stored.credential_encrypted) == _SECRET
    assert _SECRET.encode() not in stored.credential_encrypted


@pytest.mark.parametrize(
    "provider_type",
    ["gemini_interactions", "anthropic_messages"],
)
def test_create_accepts_each_provider_type(client, db_session, encryption_key, provider_type):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.post(
        "/admin/providers",
        json=_payload(name=f"Product {provider_type}", provider_type=provider_type),
    )
    assert response.status_code == 201
    assert response.json()["provider_type"] == provider_type


@pytest.mark.parametrize(
    "override",
    [
        {"rate_limit_per_minute": 0},
        {"rate_limit_per_minute": -1},
        {"monthly_budget_idr": "0"},
        {"monthly_budget_idr": "-1"},
        {"credential": "abc"},
        {"credential": "      "},
        {"credential": "  ab  "},
        {"base_url": "not-a-url"},
        {"base_url": "ftp://files.example.test/v1"},
        {"provider_type": "custom_http"},
    ],
)
def test_invalid_create_is_unprocessable(client, db_session, encryption_key, override):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.post("/admin/providers", json=_payload(**override))
    assert response.status_code == 422


def test_missing_limit_is_unprocessable(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    body = _payload()
    del body["rate_limit_per_minute"]
    response = client.post("/admin/providers", json=body)
    assert response.status_code == 422


def test_duplicate_name_is_conflict(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    assert client.post("/admin/providers", json=_payload()).status_code == 201
    response = client.post("/admin/providers", json=_payload(model_name="aiyu-2"))
    assert response.status_code == 409
    assert response.json()["code"] == "AI_PRODUCT_NAME_TAKEN"


def test_name_match_is_case_sensitive(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    assert client.post("/admin/providers", json=_payload()).status_code == 201
    response = client.post("/admin/providers", json=_payload(name="aiyu"))
    assert response.status_code == 201
    assert response.json()["name"] == "aiyu"


def test_list_returns_all_unless_is_active_is_set(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    active = client.post("/admin/providers", json=_payload(name="ActiveOne"))
    inactive = client.post("/admin/providers", json=_payload(name="InactiveOne"))
    assert active.status_code == 201
    assert inactive.status_code == 201
    turned_off = client.post(f"/admin/providers/{inactive.json()['id']}/deactivate")
    assert turned_off.status_code == 200

    everyone = client.get("/admin/providers")
    assert everyone.status_code == 200
    names = {item["name"] for item in everyone.json()}
    assert names == {"ActiveOne", "InactiveOne"}

    only_active = client.get("/admin/providers", params={"is_active": "true"})
    assert {item["name"] for item in only_active.json()} == {"ActiveOne"}

    only_inactive = client.get("/admin/providers", params={"is_active": "false"})
    assert {item["name"] for item in only_inactive.json()} == {"InactiveOne"}


def test_get_unknown_product_is_not_found(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.get(f"/admin/providers/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_patch_replaces_credential_without_returning_it(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    assert created.status_code == 201
    product_id = created.json()["id"]

    response = client.patch(
        f"/admin/providers/{product_id}",
        json={"model_name": "aiyu-2", "credential": _OTHER_SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    _assert_write_only(body, _SECRET, _OTHER_SECRET)
    assert body["credential_hint"] == _OTHER_SECRET[-4:]
    assert body["model_name"] == "aiyu-2"
    assert body["name"] == "AiYU"

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert decrypt_credential(stored.credential_encrypted) == _OTHER_SECRET


def test_patch_keeps_credential_when_omitted(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    product_id = created.json()["id"]

    response = client.patch(
        f"/admin/providers/{product_id}",
        json={"rate_limit_per_minute": 60},
    )
    assert response.status_code == 200
    assert response.json()["credential_hint"] == _SECRET[-4:]
    assert response.json()["rate_limit_per_minute"] == 60

    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert decrypt_credential(stored.credential_encrypted) == _SECRET


@pytest.mark.parametrize("credential", ["      ", "  ab  "])
def test_invalid_credential_on_patch_is_unprocessable(
    client, db_session, encryption_key, credential
):
    complete_login(client, db_session, ADMIN_SUB)
    product_id = client.post("/admin/providers", json=_payload()).json()["id"]

    response = client.patch(f"/admin/providers/{product_id}", json={"credential": credential})

    assert response.status_code == 422
    db_session.expire_all()
    stored = db_session.get(AiProduct, uuid.UUID(product_id))
    assert decrypt_credential(stored.credential_encrypted) == _SECRET


@pytest.mark.parametrize("field", ["name", "rate_limit_per_minute", "monthly_budget_idr"])
def test_patch_null_leaves_the_field_unchanged(client, db_session, encryption_key, field):
    # Semua kolom ini NOT NULL. Dulu null lolos ke database dan jatuh ke
    # handler IntegrityError, yang menjawab 409 "nama sudah dipakai".
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload()).json()

    response = client.patch(f"/admin/providers/{created['id']}", json={field: None})

    assert response.status_code == 200
    assert response.json()[field] == created[field]


def test_patch_same_name_is_not_a_conflict(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    response = client.patch(
        f"/admin/providers/{created.json()['id']}",
        json={"name": "AiYU"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "AiYU"


def test_activate_and_deactivate_keep_the_row(client, db_session, encryption_key):
    complete_login(client, db_session, ADMIN_SUB)
    created = client.post("/admin/providers", json=_payload())
    product_id = created.json()["id"]

    first_off = client.post(f"/admin/providers/{product_id}/deactivate")
    assert first_off.status_code == 200
    assert first_off.json()["is_active"] is False
    second_off = client.post(f"/admin/providers/{product_id}/deactivate")
    assert second_off.status_code == 200
    assert second_off.json()["is_active"] is False

    still_there = client.get(f"/admin/providers/{product_id}")
    assert still_there.status_code == 200
    assert still_there.json()["is_active"] is False
    _assert_write_only(still_there.json(), _SECRET)

    first_on = client.post(f"/admin/providers/{product_id}/activate")
    assert first_on.status_code == 200
    assert first_on.json()["is_active"] is True
    second_on = client.post(f"/admin/providers/{product_id}/activate")
    assert second_on.status_code == 200
    assert second_on.json()["is_active"] is True


def test_no_session_is_unauthenticated(client):
    response = client.get("/admin/providers")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_author_is_forbidden(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.post("/admin/providers", json=_payload())
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
