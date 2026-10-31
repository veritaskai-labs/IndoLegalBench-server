"""Jenis pengukuran produk AI di PostgreSQL. PBI-23 AC1-AC3, SCRUM-201.

Yang hanya bisa dibuktikan di PostgreSQL: kolom model_params bertipe JSONB, dan
constraint C1 (D1) mensyaratkan model_params IS NULL untuk produk Manual. Python
None harus tersimpan sebagai SQL NULL, bukan JSONB null, kalau tidak produk
Otomatis yang punya parameter tidak pernah bisa dipindah ke Manual.

Data di-commit karena service yang commit; setiap tes memakai nama dan id baru.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.providers import service
from app.modules.providers.models import MeasurementType
from app.modules.providers.schemas import AiProductCreate, AiProductUpdate
from app.shared.config import get_settings
from app.shared.security import Role

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def db(monkeypatch):
    """Sesi PostgreSQL dan kunci enkripsi sementara untuk kredensial produk Otomatis."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_engine(POSTGRES_URL)
    with sessionmaker(bind=engine)() as sesi:
        yield sesi
    engine.dispose()
    get_settings.cache_clear()


def _admin(db) -> uuid.UUID:
    admin = User(email=f"admin-201-{uuid.uuid4().hex}@veritask.test", name="Admin", role=Role.ADMIN)
    db.add(admin)
    db.commit()
    return admin.id


def _otomatis(nama: str, **ubah) -> AiProductCreate:
    # Nilai sintetis, bukan produk yang didaftarkan Veritask.
    isi = {
        "name": nama,
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential": "rahasia-uji-pg",  # pragma: allowlist secret
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("500000.00"),
    }
    isi.update(ubah)
    return AiProductCreate(**isi)


def test_produk_otomatis_dengan_parameter_pindah_ke_manual_di_postgres(db):
    """Edge: parameter yang dikosongkan menjadi SQL NULL, jadi C1 untuk Manual terpenuhi."""
    # Arrange
    produk = service.create_product(
        db,
        _otomatis(f"PG-201-{uuid.uuid4().hex[:8]}", model_params={"temperature": 0.3}),
        created_by=_admin(db),
    )

    # Act
    hasil = service.update_product(
        db,
        produk.id,
        AiProductUpdate(
            measurement_type=MeasurementType.MANUAL,
            vendor_name="Penyedia",
            site_url="https://contoh.test",
        ),
    )

    # Assert
    assert hasil.measurement_type == MeasurementType.MANUAL
    tersimpan = db.execute(
        text("SELECT model_params IS NULL FROM ai_products WHERE id = :id"), {"id": produk.id}
    ).scalar_one()
    assert tersimpan is True


def test_parameter_kosong_tidak_tersimpan_sebagai_jsonb_null(db):
    """Positif: produk Otomatis tanpa parameter menyimpan SQL NULL, dengan parameter hanya
    kunci yang diisi (D1: kunci kosong memakai default penyedia)."""
    # Arrange
    admin = _admin(db)

    # Act
    tanpa = service.create_product(
        db, _otomatis(f"PG-201-{uuid.uuid4().hex[:8]}"), created_by=admin
    )
    dengan = service.create_product(
        db,
        _otomatis(f"PG-201-{uuid.uuid4().hex[:8]}", model_params={"effort": "low"}),
        created_by=admin,
    )

    # Assert
    baris = dict(
        db.execute(
            text("SELECT id, model_params::text FROM ai_products WHERE id IN (:a, :b)"),
            {"a": tanpa.id, "b": dengan.id},
        ).all()
    )
    assert baris[tanpa.id] is None
    assert baris[dengan.id] == '{"effort": "low"}'
