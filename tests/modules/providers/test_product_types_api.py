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
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.providers import service
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.providers.schemas import AiProductCreate
from app.shared.config import get_settings
from app.shared.exceptions import ConflictError, ValidationError
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


# --- Catatan akses dan teks bebas Manual (review #81 poin 1 dan 2) -------------------------

_CATATAN_RAHASIA = (
    "login legal@veritask.example / password: Hunter2!2026"  # pragma: allowlist secret
)


def test_catatan_akses_berisi_kata_sandi_ditolak_dan_tidak_tercatat(admin, db_session):
    """Negatif: AC3, kata sandi tidak boleh masuk catatan akses. Ditolak 422 sebelum
    tersimpan, jadi tidak ada produk dan tidak ada baris audit yang memuatnya."""
    # Act
    respons = admin.post("/admin/providers", json=_manual(access_note=_CATATAN_RAHASIA))

    # Assert
    assert respons.status_code == 422, respons.text
    assert (respons.json()["code"], respons.json()["field"]) == (
        "ACCESS_NOTE_SECRET",
        "access_note",
    )
    assert db_session.query(AiProduct).count() == 0
    assert "Hunter2" not in str([b.after for b in db_session.query(AuditLog).all()])


def test_ubah_catatan_akses_berisi_kata_sandi_ditolak(admin, db_session):
    """Negatif: aturan yang sama berlaku saat PATCH; catatan lama tidak berubah."""
    # Arrange
    product_id = _daftar(admin, _manual(access_note="Minta akses ke Admin"))

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json={"access_note": _CATATAN_RAHASIA})

    # Assert
    assert respons.status_code == 422, respons.text
    assert respons.json()["code"] == "ACCESS_NOTE_SECRET"
    db_session.expire_all()
    assert db_session.get(AiProduct, uuid.UUID(product_id)).access_note == "Minta akses ke Admin"


@pytest.mark.parametrize("field", ["access_note", "manual_params"])
def test_teks_bebas_manual_dibatasi_2000_karakter(admin, db_session, field):
    """Edge: 2000 karakter masih diterima, 2001 ditolak 422 sebelum menyentuh database.
    Tanpa batas, teks 1 MB tersimpan dan tersalin utuh ke audit_logs."""
    # Act
    pas = admin.post("/admin/providers", json=_manual(name="Pas", **{field: "a" * 2000}))
    lebih = admin.post("/admin/providers", json=_manual(name="Lebih", **{field: "a" * 2001}))

    # Assert
    assert pas.status_code == 201, pas.text
    assert lebih.status_code == 422
    assert lebih.json()["detail"][0]["loc"] == ["body", field]
    assert [p.name for p in db_session.query(AiProduct).all()] == ["Pas"]


@pytest.mark.parametrize("field", ["access_note", "manual_params"])
def test_ubah_teks_bebas_manual_juga_dibatasi(admin, field):
    """Negatif: batas yang sama berlaku saat PATCH."""
    # Arrange
    product_id = _daftar(admin, _manual())

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json={field: "a" * 2001})

    # Assert
    assert respons.status_code == 422
    assert respons.json()["detail"][0]["loc"] == ["body", field]


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


def test_produk_otomatis_dengan_parameter_bisa_pindah_ke_manual(admin, db_session):
    """Edge: parameter model yang dikosongkan harus menjadi SQL NULL, bukan JSON null.

    C1 mensyaratkan model_params IS NULL untuk produk Manual. Tanpa none_as_null,
    produk Otomatis yang punya parameter tidak pernah bisa dipindah ke Manual.
    """
    # Arrange
    product_id = _daftar(admin, _otomatis(model_params={"temperature": 0.3}))

    # Act
    respons = admin.patch(
        f"/admin/providers/{product_id}",
        json={
            "measurement_type": "manual",
            "vendor_name": "Penyedia",
            "site_url": "https://x.test",
        },
    )

    # Assert
    assert respons.status_code == 200, respons.text
    db_session.expire_all()
    assert db_session.get(AiProduct, uuid.UUID(product_id)).model_params is None


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


# --- Audit perubahan produk (PBI-18 lewat listener) ----------------------------------------
# Diuji ujung ke ujung: perubahan lewat API, lalu baris audit_logs yang benar-benar ditulis.


def _audit(db_session, action: str) -> list[AuditLog]:
    return db_session.query(AuditLog).filter_by(action=action).order_by(AuditLog.id).all()


def test_perubahan_isian_manual_tercatat_di_audit(admin, db_session):
    """Positif: isian dari SCRUM-200 (URL situs, catatan akses) ikut dipantau audit."""
    # Arrange
    product_id = _daftar(admin, _manual())

    # Act
    admin.patch(
        f"/admin/providers/{product_id}",
        json={"site_url": "https://baru.contoh.test", "access_note": "Akun baru"},
    )

    # Assert
    [baris] = _audit(db_session, "ai_product.updated")
    assert baris.before == {"site_url": "https://contoh.test", "access_note": None}
    assert baris.after == {"site_url": "https://baru.contoh.test", "access_note": "Akun baru"}
    [daftar] = _audit(db_session, "ai_product.registered")
    assert daftar.after["measurement_type"] == "manual"


def test_audit_tidak_pernah_memuat_kredensial(admin, db_session):
    """Negatif: kredensial, ciphertext, dan hint tidak muncul di audit mana pun.

    Mencakup pendaftaran, rotasi kredensial, dan pindah ke Manual. Kredensial yang
    dihapus saat pindah jenis tercatat sebagai penanda "removed", bukan "diganti".
    """
    # Arrange
    product_id = _daftar(admin, _otomatis())
    lain = "kredensial-pengganti-201"  # pragma: allowlist secret
    ciphertext = db_session.get(AiProduct, uuid.UUID(product_id)).credential_encrypted

    # Act
    admin.patch(f"/admin/providers/{product_id}", json={"credential": lain})
    admin.patch(
        f"/admin/providers/{product_id}",
        json={
            "measurement_type": "manual",
            "vendor_name": "Penyedia",
            "site_url": "https://x.test",
        },
    )

    # Assert
    semua = str([(b.action, b.before, b.after) for b in db_session.query(AuditLog).all()])
    for rahasia in (_SECRET, lain, _SECRET[-4:], lain[-4:], ciphertext.decode(errors="ignore")):
        assert rahasia not in semua
    assert len(_audit(db_session, "ai_product.credential_rotated")) == 1
    pindah = _audit(db_session, "ai_product.updated")[-1]
    assert (pindah.before["credential"], pindah.after["credential"]) == ("stored", "removed")
    assert (pindah.before["measurement_type"], pindah.after["measurement_type"]) == (
        "automatic",
        "manual",
    )


def test_pindah_ke_otomatis_bukan_kredensial_diganti(admin, db_session):
    """Edge: kredensial pertama saat pindah Manual ke Otomatis tidak dicatat sebagai
    credential_rotated (review #81 poin 3), melainkan "removed" ke "stored" di event
    updated yang sama, kebalikan dari pindah ke Manual."""
    # Arrange
    product_id = _daftar(admin, _manual())
    body = _otomatis()
    del body["name"]

    # Act
    respons = admin.patch(f"/admin/providers/{product_id}", json=body)

    # Assert
    assert respons.status_code == 200, respons.text
    assert _audit(db_session, "ai_product.credential_rotated") == []
    [pindah] = _audit(db_session, "ai_product.updated")
    assert (pindah.before["credential"], pindah.after["credential"]) == ("removed", "stored")
    assert _SECRET not in str((pindah.before, pindah.after))


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


def _gagal_simpan(pesan: str):
    """repository.create yang gagal di database dengan pesan driver tertentu."""

    def create(_db, _produk):
        raise IntegrityError("INSERT INTO ai_products", {}, Exception(pesan))

    return create


def test_service_nama_kembar_di_database_tetap_409(monkeypatch):
    """Negatif: nama yang lolos cek awal tapi bentrok di database (balapan) tetap 409."""
    # Arrange
    repo = MagicMock()
    repo.get_by_name.return_value = None
    repo.create.side_effect = _gagal_simpan("UNIQUE constraint failed: ai_products.name")
    monkeypatch.setattr(service, "repository", repo)

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        service.create_product(MagicMock(), AiProductCreate(**_manual()), created_by=uuid.uuid4())
    assert galat.value.code == service.NAME_TAKEN


def test_service_pelanggaran_selain_nama_tidak_disamarkan_jadi_409(monkeypatch):
    """Negatif (review #81 poin 4): pembuat yang tidak ada (FK) bukan "nama sudah dipakai".

    Sebelumnya setiap IntegrityError saat create menjadi 409 NAME_TAKEN. Sekarang
    create memakai pemetaan yang sama dengan update, jadi galat lain diteruskan.
    """
    # Arrange
    repo = MagicMock()
    repo.get_by_name.return_value = None
    repo.create.side_effect = _gagal_simpan("FOREIGN KEY constraint failed")
    db = MagicMock()
    monkeypatch.setattr(service, "repository", repo)

    # Act + Assert
    with pytest.raises(IntegrityError):
        service.create_product(db, AiProductCreate(**_manual()), created_by=uuid.uuid4())
    db.rollback.assert_called_once()
