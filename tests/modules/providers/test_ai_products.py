"""SCRUM-75 SCRUM-114 / SCRUM-133: ai_products schema.

SQLite in-memory, same as the other model tests. These lock the columns
and checks the ticket names: unique active name (partial index), positive
limits, lowercase enum values, last-test fields, and soft delete.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.providers.models import (
    AiProduct,
    LastTestStatus,
    MeasurementType,
    ProviderType,
)
from app.modules.providers.schemas import AiProductCreate, AiProductUpdate, _teks_bersih
from app.modules.providers.service import (
    _as_bytes,
    _enkripsi,
    create_product,
    update_product,
)
from app.modules.providers.service import (
    test_connection as service_test_connection,
)
from app.shared.exceptions import ConflictError, InternalError, ValidationError
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


def test_deleted_name_cannot_be_reused(db_session):
    # Arrange: produk pertama sudah dihapus (deleted_at diisi)
    admin = _admin(db_session)
    first = _product(admin.id, deleted_at=datetime.now(UTC))
    db_session.add(first)
    db_session.commit()

    # Act & Assert: nama unik selamanya (C3), sehingga nama produk terhapus tetap ditolak
    second = _product(admin.id, model_name="aiyu-2")
    db_session.add(second)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_new_product_has_no_deleted_at(db_session):
    # Arrange & Act
    admin = _admin(db_session)
    product = _product(admin.id)
    db_session.add(product)
    db_session.commit()

    # Assert
    assert product.deleted_at is None


def test_new_product_defaults_to_automatic(db_session):
    admin = _admin(db_session)
    product = _product(admin.id)
    db_session.add(product)
    db_session.commit()

    assert product.measurement_type == MeasurementType.AUTOMATIC


def test_manual_product_can_be_stored(db_session):
    admin = _admin(db_session)
    manual_prod = AiProduct(
        name="Justisio",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="PT Justisio AI",
        site_url="https://justisio.test",
        access_note="Akun Pro tim legal",
        manual_params="mode pro",
        created_by=admin.id,
    )
    db_session.add(manual_prod)
    db_session.commit()

    assert manual_prod.id is not None
    assert manual_prod.measurement_type == MeasurementType.MANUAL
    assert manual_prod.vendor_name == "PT Justisio AI"
    assert manual_prod.provider_type is None
    assert manual_prod.credential_encrypted is None


def test_manual_product_with_automatic_fields_violates_check(db_session):
    admin = _admin(db_session)
    # Manual tidak boleh punya rate_limit_per_minute / credential dsb
    manual_prod = AiProduct(
        name="InvalidManual",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Vendor",
        site_url="https://vendor.test",
        rate_limit_per_minute=30,
        created_by=admin.id,
    )
    db_session.add(manual_prod)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_automatic_product_missing_credentials_violates_check(db_session):
    admin = _admin(db_session)
    # Otomatis wajib punya credential_encrypted dsb
    auto_prod = AiProduct(
        name="InvalidAuto",
        measurement_type=MeasurementType.AUTOMATIC,
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.test",
        model_name="m1",
        rate_limit_per_minute=30,
        monthly_budget_idr=Decimal("1000000.00"),
        created_by=admin.id,
    )
    db_session.add(auto_prod)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_automatic_product_with_manual_fields_violates_check(db_session):
    admin = _admin(db_session)
    # Otomatis tidak boleh punya vendor_name / site_url
    auto_prod = _product(admin.id, vendor_name="Vendor X")
    db_session.add(auto_prod)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_measurement_type_can_be_changed_without_history(db_session):
    admin = _admin(db_session)
    prod = _product(admin.id, name="DeepSeek-R1")
    db_session.add(prod)
    db_session.commit()

    # Ubah dari automatic ke manual sebelum punya riwayat
    updated = update_product(
        db_session,
        prod.id,
        AiProductUpdate(
            measurement_type=MeasurementType.MANUAL,
            vendor_name="DeepSeek Inc",
            site_url="https://chat.deepseek.com",
        ),
    )

    assert updated.measurement_type == MeasurementType.MANUAL
    assert updated.vendor_name == "DeepSeek Inc"
    assert updated.site_url == "https://chat.deepseek.com"
    assert updated.provider_type is None
    assert updated.credential_hint is None


def test_measurement_type_cannot_be_changed_with_history(db_session):
    admin = _admin(db_session)
    prod = _product(admin.id, name="DeepSeek-R2")
    db_session.add(prod)
    db_session.commit()

    # Buat tabel measurement_sessions tiruan sementara untuk menguji deteksi riwayat
    db_session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS measurement_sessions (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL
            )
            """
        )
    )
    db_session.execute(
        text("INSERT INTO measurement_sessions (id, product_id) VALUES ('sess-1', :pid)"),
        {"pid": str(prod.id)},
    )
    db_session.commit()

    # Coba ubah jenis saat sudah ada riwayat
    with pytest.raises(ValidationError) as exc_info:
        update_product(
            db_session,
            prod.id,
            AiProductUpdate(
                measurement_type=MeasurementType.MANUAL,
                vendor_name="DeepSeek Inc",
                site_url="https://chat.deepseek.com",
            ),
        )

    assert exc_info.value.code == "MEASUREMENT_TYPE_IMMUTABLE"
    assert exc_info.value.status_code == 422


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


def test_manual_to_automatic_type_switch_clears_manual_fields(db_session, monkeypatch):
    from cryptography.fernet import Fernet

    from app.shared.config import get_settings

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    admin = _admin(db_session)
    manual_prod = AiProduct(
        name="ManualToAuto",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Manual Vendor",
        site_url="https://manual.test",
        access_note="Notes",
        manual_params="Params",
        created_by=admin.id,
    )
    db_session.add(manual_prod)
    db_session.commit()

    updated = update_product(
        db_session,
        manual_prod.id,
        AiProductUpdate(
            measurement_type=MeasurementType.AUTOMATIC,
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://api.example.test",
            model_name="model-x",
            credential="new-secret-key",  # pragma: allowlist secret
            rate_limit_per_minute=20,
            monthly_budget_idr=Decimal("500000.00"),
        ),
    )

    assert updated.measurement_type == MeasurementType.AUTOMATIC
    assert updated.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert updated.vendor_name is None
    assert updated.site_url is None
    assert updated.access_note is None
    assert updated.manual_params is None


def test_test_connection_rejects_manual_product(db_session):
    admin = _admin(db_session)
    manual_prod = AiProduct(
        name="Justisio-Test",
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Vendor",
        site_url="https://vendor.test",
        created_by=admin.id,
    )
    db_session.add(manual_prod)
    db_session.commit()

    with pytest.raises(ValidationError) as exc_info:
        service_test_connection(db_session, manual_prod.id)
    assert exc_info.value.field == "measurement_type"
    assert "Produk manual tidak memiliki uji koneksi" in str(exc_info.value)


def test_test_connection_rejects_missing_credentials(db_session):
    admin = _admin(db_session)
    prod = AiProduct(
        name="AutoNoCred",
        measurement_type=MeasurementType.AUTOMATIC,
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.test",
        model_name="model-x",
        rate_limit_per_minute=20,
        monthly_budget_idr=Decimal("500000.00"),
        created_by=admin.id,
    )
    from unittest.mock import patch

    with (
        patch("app.modules.providers.service._wajib_ada", return_value=prod),
        pytest.raises(ValidationError) as exc_info,
    ):
        service_test_connection(db_session, prod.id)
    assert exc_info.value.field == "credential"
    assert "Kredensial produk belum diisi" in str(exc_info.value)


def test_test_connection_decrypt_failure_raises_internal_error(db_session):
    admin = _admin(db_session)
    prod = _product(admin.id, credential_encrypted=b"corrupted-ciphertext")
    db_session.add(prod)
    db_session.commit()

    from unittest.mock import patch

    from app.modules.providers.crypto import CredentialError

    with (
        patch(
            "app.modules.providers.service.decrypt_credential",
            side_effect=CredentialError("invalid"),
        ),
        pytest.raises(InternalError) as exc_info,
    ):
        service_test_connection(db_session, prod.id)
    assert "credential could not be decrypted" in str(exc_info.value)


def test_service_create_and_update_integrity_error_raises_conflict(db_session, monkeypatch):
    from cryptography.fernet import Fernet

    from app.shared.config import get_settings

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    admin = _admin(db_session)
    payload_create = AiProductCreate(
        name="AiYU-Unique",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.example.test",
        model_name="model-x",
        credential="secret-pass",  # pragma: allowlist secret
        rate_limit_per_minute=10,
        monthly_budget_idr=Decimal("10000.00"),
    )
    from unittest.mock import patch

    with (
        patch(
            "app.modules.providers.repository.create",
            side_effect=IntegrityError("stmt", {}, Exception("dup")),
        ),
        pytest.raises(ConflictError),
    ):
        create_product(db_session, payload_create, created_by=admin.id)

    prod = _product(admin.id, name="AiYU-Existing")
    db_session.add(prod)
    db_session.commit()

    with (
        patch(
            "app.modules.providers.repository.save",
            side_effect=IntegrityError("stmt", {}, Exception("dup")),
        ),
        pytest.raises(ConflictError),
    ):
        update_product(db_session, prod.id, AiProductUpdate(name="NewName"))


def test_as_bytes_with_non_bytes():
    assert _as_bytes(bytearray(b"test")) == b"test"
    assert _as_bytes(b"test") == b"test"


def test_enkripsi_missing_key_raises_validation_error(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "")
    from app.shared.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        _enkripsi("secret-text")
    assert exc_info.value.field == "credential"


def test_schemas_whitespace_and_update_base_url():
    with pytest.raises(PydanticValidationError):
        AiProductUpdate(name="   ")

    with pytest.raises(PydanticValidationError):
        AiProductUpdate(vendor_name="   ")

    with pytest.raises(ValueError, match="tidak boleh kosong"):
        _teks_bersih("   ")

    update_none_url = AiProductUpdate(base_url=None)
    assert update_none_url.base_url is None

    update_valid_url = AiProductUpdate(base_url="https://api.example.com")
    assert update_valid_url.base_url == "https://api.example.com"

    with pytest.raises(PydanticValidationError):
        AiProductUpdate(base_url="not-http-url")
