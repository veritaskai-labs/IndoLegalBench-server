"""SCRUM-75 SCRUM-114 / SCRUM-133: ai_products schema.

SQLite in-memory, same as the other model tests. These lock the columns
and checks the ticket names: unique active name (partial index), positive
limits, lowercase enum values, last-test fields, and soft delete.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, LastTestStatus, ProviderType
from app.shared.security import Role


def _admin(db_session) -> User:
    user = User(email="admin@veritask.test", name="Admin", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


def _product(created_by, **override) -> AiProduct:
    values = {
        # TODO(SCRUM-113): stand-in row, not a product Veritask has registered.
        # Jira only fixed the columns. Name, URL, model, limits, and the
        # dummy ciphertext are invented so the schema checks have a valid row.
        # credential_encrypted is not Fernet output; crypto tests cover that.
        "name": "AiYU",
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1",
        "model_name": "aiyu-1",
        "credential_encrypted": b"ciphertext-not-the-secret",
        "credential_hint": "cret",
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("1500000.00"),
        "created_by": created_by,
    }
    values.update(override)
    return AiProduct(**values)


def test_new_product_is_active_and_untested(db_session):
    admin = _admin(db_session)
    product = _product(admin.id)
    db_session.add(product)
    db_session.commit()

    assert product.is_active is True
    assert product.created_at is not None
    assert product.updated_at is not None
    assert product.last_test_at is None
    assert product.last_test_status is None
    assert product.last_test_message is None


def test_provider_type_is_stored_lowercase(db_session):
    admin = _admin(db_session)
    db_session.add(_product(admin.id))
    db_session.commit()

    stored = db_session.execute(text("SELECT provider_type FROM ai_products")).scalar_one()
    assert stored == "openai_compatible"


def test_each_provider_type_can_be_stored(db_session):
    admin = _admin(db_session)
    for index, provider_type in enumerate(ProviderType):
        db_session.add(_product(admin.id, name=f"Product {index}", provider_type=provider_type))
    db_session.commit()

    stored = (
        db_session.execute(text("SELECT provider_type FROM ai_products ORDER BY name"))
        .scalars()
        .all()
    )
    assert stored == ["openai_compatible", "gemini_interactions", "anthropic_messages"]


def test_last_test_status_is_stored_lowercase(db_session):
    admin = _admin(db_session)
    db_session.add(_product(admin.id, last_test_status=LastTestStatus.FAILED))
    db_session.commit()

    stored = db_session.execute(text("SELECT last_test_status FROM ai_products")).scalar_one()
    assert stored == "failed"


def test_duplicate_active_name_is_rejected(db_session):
    # Arrange: dua produk aktif dengan nama sama
    admin = _admin(db_session)
    db_session.add(_product(admin.id))
    db_session.commit()

    # Act & Assert: partial unique index menolak duplikat selama deleted_at IS NULL
    db_session.add(_product(admin.id, model_name="aiyu-2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleted_name_can_be_reused(db_session):
    # Arrange: produk pertama sudah dihapus (deleted_at diisi)
    admin = _admin(db_session)
    first = _product(admin.id, deleted_at=datetime.now(UTC))
    db_session.add(first)
    db_session.commit()

    # Act: produk baru dengan nama yang sama boleh dibuat
    second = _product(admin.id, model_name="aiyu-2")
    db_session.add(second)
    db_session.commit()  # tidak boleh raise IntegrityError

    # Assert
    assert second.id != first.id
    assert second.name == first.name
    assert second.deleted_at is None


def test_new_product_has_no_deleted_at(db_session):
    # Arrange & Act
    admin = _admin(db_session)
    product = _product(admin.id)
    db_session.add(product)
    db_session.commit()

    # Assert
    assert product.deleted_at is None


@pytest.mark.parametrize("rate_limit", [0, -1])
def test_rate_limit_must_be_positive(db_session, rate_limit):
    admin = _admin(db_session)
    db_session.add(_product(admin.id, rate_limit_per_minute=rate_limit))

    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize("budget", [Decimal("0"), Decimal("-1")])
def test_monthly_budget_must_be_positive(db_session, budget):
    admin = _admin(db_session)
    db_session.add(_product(admin.id, monthly_budget_idr=budget))

    with pytest.raises(IntegrityError):
        db_session.commit()
