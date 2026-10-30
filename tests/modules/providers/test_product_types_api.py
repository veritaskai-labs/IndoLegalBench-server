"""Mendaftarkan dan mengubah produk AI sesuai jenisnya. PBI-23 AC1-AC3, SCRUM-201.

- POST /admin/providers menerima measurement_type; tanpa field itu produk dianggap
  Otomatis, supaya client PBI-10 yang sudah ada tetap berjalan.
- Isian yang tidak sesuai jenis ditolak 422 dengan code dan field yang jelas
  (FIELD_REQUIRED / FIELD_NOT_ALLOWED), sebelum menyentuh database. Sebelumnya
  pelanggaran C1 lewat PATCH jatuh sebagai 409 "nama sudah dipakai" (review #79 poin 1).

Label di docstring: Positif, Negatif, Edge.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.modules.providers import service
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.providers.schemas import AiProductCreate
from app.shared.config import get_settings
from app.shared.exceptions import ValidationError
from app.shared.security import Role

_SECRET = "provider-token-uji-201"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """Kunci Fernet sementara; kredensial produk Otomatis selalu dienkripsi saat simpan."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _otomatis(**ubah) -> dict:
    # Nilai sintetis, bukan produk yang didaftarkan Veritask.
    body = {
        "name": "Otomatis Uji",
        "measurement_type": "automatic",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential": _SECRET,
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": "1500000.00",
    }
    body.update(ubah)
    return body


def _manual(**ubah) -> dict:
    body = {
        "name": "Manual Uji",
        "measurement_type": "manual",
        "vendor_name": "Penyedia Uji",
        "site_url": "https://contoh.test",
    }
    body.update(ubah)
    return body


@pytest.fixture
def admin(as_role):
    return as_role(Role.ADMIN)


# --- Mendaftarkan produk ------------------------------------------------------------------


def test_admin_mendaftarkan_produk_manual_tanpa_kredensial(admin, db_session):
    """Positif: produk Manual cukup nama, penyedia, URL, catatan, dan parameter teks (AC3)."""
    # Act
    respons = admin.post(
        "/admin/providers",
        json=_manual(access_note="Akun tim legal", manual_params="Mode default"),
    )

    # Assert
    assert respons.status_code == 201, respons.text
    body = respons.json()
    assert body["measurement_type"] == "manual"
    assert (body["vendor_name"], body["site_url"]) == ("Penyedia Uji", "https://contoh.test")
    assert body["has_credential"] is False
    assert body["provider_type"] is None
    assert body["rate_limit_per_minute"] is None


def test_admin_mendaftarkan_produk_otomatis_dengan_parameter_model(admin, db_session):
    """Positif dan edge: parameter model tersimpan hanya kunci yang diisi (AC2, D1).

    Kunci kosong tidak boleh tersimpan sebagai null, karena D1 meminta kunci yang
    kosong tidak dikirim ke penyedia sehingga default penyedia yang dipakai.
    """
    # Act
    respons = admin.post(
        "/admin/providers",
        json=_otomatis(model_params={"temperature": 0.3}, price_input_usd_per_mtok="0.27"),
    )

    # Assert
    assert respons.status_code == 201, respons.text
    body = respons.json()
    assert body["measurement_type"] == "automatic"
    assert body["has_credential"] is True
    assert body["model_params"]["temperature"] == 0.3
    assert db_session.query(AiProduct).one().model_params == {"temperature": 0.3}


def test_tanpa_jenis_produk_dianggap_otomatis(admin):
    """Edge: body lama PBI-10 tanpa measurement_type tetap diterima sebagai Otomatis."""
    # Arrange
    body = _otomatis()
    del body["measurement_type"]

    # Act
    respons = admin.post("/admin/providers", json=body)

    # Assert
    assert respons.status_code == 201, respons.text
    assert respons.json()["measurement_type"] == "automatic"


@pytest.mark.parametrize(
    ("body", "kode", "field"),
    [
        (_manual(credential=_SECRET), "FIELD_NOT_ALLOWED_FOR_TYPE", "credential"),
        (_manual(rate_limit_per_minute=30), "FIELD_NOT_ALLOWED_FOR_TYPE", "rate_limit_per_minute"),
        (_otomatis(vendor_name="Penyedia"), "FIELD_NOT_ALLOWED_FOR_TYPE", "vendor_name"),
        (_otomatis(credential=None), "FIELD_REQUIRED_FOR_TYPE", "credential"),
        (_manual(site_url=None), "FIELD_REQUIRED_FOR_TYPE", "site_url"),
    ],
    ids=[
        "manual-dengan-kredensial",
        "manual-dengan-batas",
        "otomatis-dengan-penyedia",
        "otomatis-tanpa-kredensial",
        "manual-tanpa-url",
    ],
)
def test_isian_tidak_sesuai_jenis_ditolak_dan_tidak_tersimpan(admin, db_session, body, kode, field):
    """Negatif: 422 dengan code dan field, dan tidak ada baris yang tersimpan."""
    # Act
    respons = admin.post("/admin/providers", json=body)

    # Assert
    assert respons.status_code == 422, respons.text
    assert (respons.json()["code"], respons.json()["field"]) == (kode, field)
    assert db_session.query(AiProduct).count() == 0


def test_catatan_akses_wajib_bila_flag_konfigurasi_menyala(admin, monkeypatch):
    """Negatif: flag AI_PRODUCT_ACCESS_NOTE_REQUIRED membuat catatan akses wajib (TBA klien)."""
    # Arrange
    monkeypatch.setenv("AI_PRODUCT_ACCESS_NOTE_REQUIRED", "true")
    get_settings.cache_clear()

    # Act
    respons = admin.post("/admin/providers", json=_manual())

    # Assert
    assert respons.status_code == 422
    assert (respons.json()["code"], respons.json()["field"]) == (
        "FIELD_REQUIRED_FOR_TYPE",
        "access_note",
    )


# --- Mengubah produk -----------------------------------------------------------------------


def _daftar(admin, body: dict) -> str:
    respons = admin.post("/admin/providers", json=body)
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def test_isian_manual_di_produk_otomatis_ditolak_422_bukan_409(admin):
    """Negatif: sebelumnya C1 menolak di database dan jatuh sebagai 409 nama dipakai."""
    # Arrange
    product_id = _daftar(admin, _otomatis())

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json={"vendor_name": "Vendor X"})

    # Assert
    assert respons.status_code == 422, respons.text
    assert (respons.json()["code"], respons.json()["field"]) == (
        "FIELD_NOT_ALLOWED_FOR_TYPE",
        "vendor_name",
    )


def test_pindah_ke_manual_tanpa_isian_manual_ditolak(admin, db_session):
    """Negatif: pindah jenis wajib membawa isian wajib jenis barunya; produk tidak berubah."""
    # Arrange
    product_id = _daftar(admin, _otomatis())

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json={"measurement_type": "manual"})

    # Assert
    assert respons.status_code == 422, respons.text
    assert (respons.json()["code"], respons.json()["field"]) == (
        "FIELD_REQUIRED_FOR_TYPE",
        "vendor_name",
    )
    db_session.expire_all()
    produk = db_session.get(AiProduct, uuid.UUID(product_id))
    assert produk.measurement_type == MeasurementType.AUTOMATIC
    assert produk.credential_encrypted is not None


def test_ubah_isian_sesuai_jenis_tetap_boleh(admin):
    """Positif: perubahan biasa tanpa mengirim ulang kredensial tetap diterima (PBI-10)."""
    # Arrange
    product_id = _daftar(admin, _manual())

    # Act
    respons = admin.patch(
        f"/admin/providers/{product_id}", json={"site_url": "https://baru.contoh.test"}
    )

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json()["site_url"] == "https://baru.contoh.test"


def test_pindah_ke_otomatis_wajib_kredensial_baru(admin):
    """Edge: produk Manual tidak punya kredensial, jadi pindah ke Otomatis wajib mengirimnya."""
    # Arrange
    product_id = _daftar(admin, _manual())
    body = _otomatis()
    del body["name"], body["credential"]

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json=body)

    # Assert
    assert respons.status_code == 422, respons.text
    assert (respons.json()["code"], respons.json()["field"]) == (
        "FIELD_REQUIRED_FOR_TYPE",
        "credential",
    )


# --- Service dengan repository di-mock -----------------------------------------------------
# Repository diganti MagicMock, supaya yang diuji hanya urutan keputusan service:
# isian dicek sebelum produk dibuat, dan produk Manual tidak pernah dienkripsi.


def test_service_menolak_sebelum_menyimpan(monkeypatch):
    """Negatif: isian salah berhenti di service, repository.create tidak dipanggil."""
    # Arrange
    repo = MagicMock()
    repo.get_by_name.return_value = None
    monkeypatch.setattr(service, "repository", repo)
    payload = AiProductCreate(**_manual(credential=_SECRET))

    # Act
    with pytest.raises(ValidationError):
        service.create_product(MagicMock(), payload, created_by=uuid.uuid4())

    # Assert
    repo.create.assert_not_called()


def _seperti_tersimpan(_db, produk: AiProduct) -> AiProduct:
    """Isi kolom yang biasanya diberi database, supaya response bisa dibentuk."""
    produk.id, produk.is_active = uuid.uuid4(), True
    produk.created_at = produk.updated_at = datetime.now(UTC)
    return produk


def test_service_produk_manual_tidak_menyimpan_kredensial(monkeypatch):
    """Positif: produk Manual dibuat tanpa enkripsi dan tanpa isian Otomatis."""
    # Arrange
    repo = MagicMock()
    repo.get_by_name.return_value = None
    repo.create.side_effect = _seperti_tersimpan
    enkripsi = MagicMock()
    monkeypatch.setattr(service, "repository", repo)
    monkeypatch.setattr(service, "_enkripsi", enkripsi)

    # Act
    service.create_product(MagicMock(), AiProductCreate(**_manual()), created_by=uuid.uuid4())

    # Assert
    enkripsi.assert_not_called()
    produk = repo.create.call_args.args[1]
    assert produk.measurement_type == MeasurementType.MANUAL
    assert (produk.credential_encrypted, produk.provider_type) == (None, None)
