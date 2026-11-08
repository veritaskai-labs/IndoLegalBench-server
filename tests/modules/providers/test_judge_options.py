"""Pilihan penilai berasal dari produk AI otomatis. PBI-14 AC1, SCRUM-78 SCRUM-271.

Dropdown penilai di form pengukuran memakai provider yang sudah terdaftar di halaman
Provider, jadi GET /judge-providers membaca ai_products (jenis otomatis, aktif, belum
dihapus), bukan registri judge_providers.

Label: Positif = produk muncul, Negatif = produk tidak boleh muncul, Edge = batas aturan.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.providers.models import JudgeProvider, MeasurementType, ProviderType
from app.shared.security import Role
from tests.judge_rows import tambah_penilai
from tests.modules.conftest import USER_ID_QA
from tests.modules.runs.test_summary_endpoint import _tambah_produk

URL = "/judge-providers"


@pytest.fixture
def admin(as_role):
    return as_role(Role.ADMIN)


def test_produk_otomatis_aktif_muncul_dengan_isian_minimal(admin, db_session):
    """Positif: dropdown hanya menerima id, nama, jenis provider, dan model."""
    # Arrange
    produk = tambah_penilai(db_session, USER_ID_QA, name="DeepSeek V4-Flash")

    # Act
    response = admin.get(URL)

    # Assert
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(produk.id),
            "name": "DeepSeek V4-Flash",
            "provider_type": "openai_compatible",
            "model_name": "judge-model-1",
        }
    ]


def test_urut_nama(admin, db_session):
    """Edge: urutan tetap berdasarkan nama, supaya dropdown tidak melompat."""
    # Arrange
    tambah_penilai(db_session, USER_ID_QA, name="Zeta")
    tambah_penilai(db_session, USER_ID_QA, name="Alfa")

    # Act
    nama = [item["name"] for item in admin.get(URL).json()]

    # Assert
    assert nama == ["Alfa", "Zeta"]


@pytest.mark.parametrize(
    "ubah",
    [
        {"is_active": False},
        {"deleted_at": datetime(2026, 11, 1, tzinfo=UTC)},
    ],
    ids=["nonaktif", "terhapus"],
)
def test_produk_nonaktif_atau_terhapus_tidak_muncul(admin, db_session, ubah):
    """Negatif: produk yang tidak bisa dipakai tidak ditawarkan."""
    # Arrange
    tambah_penilai(db_session, USER_ID_QA, **ubah)

    # Act
    response = admin.get(URL)

    # Assert
    assert response.json() == []


def test_produk_manual_tidak_muncul(admin, db_session):
    """Negatif: produk manual tidak punya kredensial, jadi tidak bisa menjadi penilai."""
    # Arrange
    _tambah_produk(
        db_session,
        name="Justisio",
        measurement_type=MeasurementType.MANUAL,
        provider_type=None,
        base_url=None,
        model_name=None,
        credential_encrypted=None,
        credential_hint=None,
        rate_limit_per_minute=None,
        monthly_budget_idr=None,
        vendor_name="Justisio",
        site_url="https://justisio.test",
    )

    # Act
    response = admin.get(URL)

    # Assert
    assert response.json() == []


def test_registri_judge_providers_lama_tidak_ikut_muncul(admin, db_session):
    """Negatif: baris judge_providers tidak lagi menjadi sumber dropdown."""
    # Arrange
    db_session.add(
        JudgeProvider(
            name="Registri lama",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="https://judge.example.test/v1",
            model_name="judge-lama",
            credential_encrypted=b"terenkripsi",
            credential_hint="7kQz",
            price_input_usd_per_mtok=Decimal("0.27"),
            price_output_usd_per_mtok=Decimal("1.1"),
            monthly_budget_idr=Decimal("500000.00"),
            created_by=USER_ID_QA,
        )
    )
    db_session.commit()

    # Act
    response = admin.get(URL)

    # Assert
    assert response.json() == []


def test_developer_ai_melihat_pilihan_penilai(as_role, db_session):
    """Positif: Developer AI boleh membuat pengukuran (No. 35), jadi butuh daftarnya."""
    # Arrange
    tambah_penilai(db_session, USER_ID_QA, name="DeepSeek penilai")

    # Act
    response = as_role(Role.DEVELOPER_AI).get(URL)

    # Assert
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["DeepSeek penilai"]


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_ditolak_403(as_role, role):
    """Negatif: selain Admin dan Developer AI tidak boleh membaca daftar penilai."""
    # Act
    response = as_role(role).get(URL)

    # Assert
    assert response.status_code == 403
