"""Logika bisnis modul providers.

PBI-10 Registri produk AI yang akan diukur.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.providers import repository, rules, visibility
from app.modules.providers.adapters.dispatch import run_connection_test
from app.modules.providers.adapters.http import clip
from app.modules.providers.crypto import (
    CredentialError,
    credential_hint,
    decrypt_credential,
    encrypt_credential,
)
from app.modules.providers.models import (
    AiProduct,
    ConnectionTestErrorCategory,
    LastTestStatus,
    MeasurementType,
    ProviderType,
)
from app.modules.providers.schemas import (
    AiProductCreate,
    AiProductOption,
    AiProductRead,
    AiProductUpdate,
    ConnectionTestRead,
)
from app.shared.config import get_settings
from app.shared.exceptions import (
    ConflictError,
    ForbiddenError,
    InternalError,
    NotFoundError,
    ValidationError,
)
from app.shared.security import Role

NAME_TAKEN = "AI_PRODUCT_NAME_TAKEN"

# Label penyedia produk Otomatis di daftar (PBI-23 AC6), sama dengan client
# (src/lib/providers/format.ts) supaya daftar dan detail menulis nama yang sama.
_LABEL_PROVIDER = {
    ProviderType.OPENAI_COMPATIBLE: "OpenAI-compatible",
    ProviderType.GEMINI_INTERACTIONS: "Gemini Interactions",
    ProviderType.ANTHROPIC_MESSAGES: "Anthropic Messages",
}


def create_product(
    db: Session, payload: AiProductCreate, *, created_by: uuid.UUID
) -> AiProductRead:
    jenis = payload.measurement_type
    isian = payload.model_dump(include=set(_SEMUA_ISIAN_JENIS))
    _pastikan_isian_sesuai_jenis(jenis, isian)
    _pastikan_nama_bebas(db, payload.name)
    # Hanya isian milik jenis ini yang ditulis; kredensial hanya ada di produk Otomatis.
    credential = isian.pop("credential")
    milik_jenis = {field: isian[field] for field in rules.TYPE_FIELDS[jenis] if field in isian}
    product = AiProduct(
        name=payload.name, measurement_type=jenis, created_by=created_by, **milik_jenis
    )
    if credential is not None:
        product.credential_encrypted = _enkripsi(credential)
        product.credential_hint = credential_hint(credential)
    try:
        tersimpan = repository.create(db, product)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            f"Nama produk AI '{payload.name}' sudah dipakai",
            code=NAME_TAKEN,
        ) from None
    return _tampilkan(tersimpan)


def get_product(db: Session, product_id: uuid.UUID) -> AiProductRead:
    return _tampilkan(_wajib_ada(db, product_id))


def list_products(
    db: Session, *, is_active: bool | None, measurement_type: MeasurementType | None = None
) -> list[AiProductRead]:
    """Daftar Admin, disaring status dan jenis (PBI-23 AC6)."""
    produk = repository.list_products(db, is_active=is_active, measurement_type=measurement_type)
    return [_tampilkan(item) for item in produk]


def list_options(
    db: Session, *, purpose: visibility.ProductPurpose, role: Role
) -> list[AiProductOption]:
    """Pilihan produk untuk satu tujuan: hanya produk aktif berjenis sesuai (AC7, AC8).

    Peran diperiksa sebelum membaca produk, jadi peran yang tidak berhak tidak
    pernah memicu query.
    """
    if not visibility.can_list_options(role, purpose):
        raise ForbiddenError("Peran Anda tidak berwenang melihat daftar produk untuk tujuan ini")
    produk = repository.list_products(
        db, is_active=True, measurement_type=visibility.measurement_type_for(purpose)
    )
    return [
        AiProductOption(id=item.id, name=item.name, measurement_type=item.measurement_type)
        for item in produk
    ]


def update_product(db: Session, product_id: uuid.UUID, payload: AiProductUpdate) -> AiProductRead:
    product = _wajib_ada(db, product_id)
    # null berarti "tidak diubah": semua kolom ini NOT NULL, jadi null tidak boleh ditulis.
    perubahan = payload.model_dump(exclude_unset=True, exclude_none=True)
    credential = perubahan.pop("credential", None)

    # Immutability & Type switching check (C2: terkunci setelah punya riwayat pengukuran)
    tipe_baru = perubahan.pop("measurement_type", product.measurement_type)
    berganti = tipe_baru != product.measurement_type
    if berganti and repository.has_measurement_sessions(db, product.id):
        raise ValidationError(
            "Jenis pengukuran tidak dapat diubah karena produk sudah memiliki riwayat pengukuran",
            code="MEASUREMENT_TYPE_IMMUTABLE",
            field="measurement_type",
        )
    # SCRUM-201: kombinasi akhir dicek sebelum produk diubah, jadi isian yang tidak
    # sesuai jenis menjadi 422 dengan field-nya, bukan pelanggaran C1 di database.
    _pastikan_isian_sesuai_jenis(
        tipe_baru, _isian_setelah_ubah(product, perubahan, credential, berganti=berganti)
    )
    if berganti:
        # Belum punya riwayat: reset isian khusus jenis lama sesuai AC5 & D1 C2
        product.measurement_type = tipe_baru
        if tipe_baru == MeasurementType.MANUAL:
            # Otomatis -> Manual
            product.provider_type = None
            product.base_url = None
            product.model_name = None
            product.credential_encrypted = None
            product.credential_hint = None
            product.rate_limit_per_minute = None
            product.monthly_budget_idr = None
            product.last_test_at = None
            product.last_test_status = None
            product.last_test_message = None
            product.last_test_error_category = None
            product.model_params = None
            product.price_input_usd_per_mtok = None
            product.price_output_usd_per_mtok = None
        else:
            # Manual -> Otomatis
            product.vendor_name = None
            product.site_url = None
            product.access_note = None
            product.manual_params = None

    if "name" in perubahan:
        _pastikan_nama_bebas(db, perubahan["name"], kecuali=product.id)
        product.name = perubahan.pop("name")

    for field, value in perubahan.items():
        setattr(product, field, value)

    if credential is not None:
        product.credential_encrypted = _enkripsi(credential)
        product.credential_hint = credential_hint(credential)

    try:
        tersimpan = repository.save(db, product)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            f"Nama produk AI '{product.name}' sudah dipakai",
            code=NAME_TAKEN,
        ) from None
    return _tampilkan(tersimpan)


def delete_product(db: Session, product_id: uuid.UUID) -> None:
    """Soft delete: mengisi deleted_at. Produk tidak muncul di daftar manapun setelah ini."""
    product = _wajib_ada(db, product_id)
    product.deleted_at = datetime.now(UTC)
    repository.save(db, product)


def set_active(db: Session, product_id: uuid.UUID, *, is_active: bool) -> AiProductRead:
    product = _wajib_ada(db, product_id)
    product.is_active = is_active
    return _tampilkan(repository.save(db, product))


def product_names(db: Session, product_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Nama produk AI untuk kolom Objek audit log (F-06).

    Produk yang sudah dihapus tetap bernama, supaya catatan
    ai_product.deleted masih terbaca. Id tanpa baris produk tidak ikut.
    """
    if not product_ids:
        return {}
    return repository.names_by_ids(db, product_ids)


def test_connection(db: Session, product_id: uuid.UUID) -> ConnectionTestRead:
    """Uji koneksi satu kali. Produk nonaktif tetap boleh diuji.

    Dekripsi hanya untuk panggilan ini. Gagal dekripsi jadi 500 dan
    last_test_* tidak diubah. Kegagalan provider tetap HTTP 200.
    """
    product = _wajib_ada(db, product_id)
    if product.measurement_type == MeasurementType.MANUAL:
        raise ValidationError("Produk manual tidak memiliki uji koneksi", field="measurement_type")

    if product.credential_encrypted is None:
        raise ValidationError("Kredensial produk belum diisi", field="credential")
    try:
        secret = decrypt_credential(_as_bytes(product.credential_encrypted))
    except CredentialError:
        raise InternalError("credential could not be decrypted") from None

    result = run_connection_test(
        provider_type=_provider_value(product.provider_type),
        base_url=product.base_url,
        model_name=product.model_name,
        api_key=secret,
    )
    is_ok = result.status == "ok"
    message = None if is_ok else clip(result.message or "connection test failed", secret)
    category = None if is_ok else ConnectionTestErrorCategory(result.error_category or "unknown")
    product.last_test_at = datetime.now(UTC)
    product.last_test_status = LastTestStatus.OK if is_ok else LastTestStatus.FAILED
    product.last_test_message = message
    product.last_test_error_category = category
    repository.save(db, product)

    if is_ok:
        return ConnectionTestRead(
            status="ok", latency_ms=result.latency_ms if result.latency_ms is not None else 0
        )
    return ConnectionTestRead(status="failed", message=message, error_category=category)


def _as_bytes(value: bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    return bytes(value)


def _provider_value(provider_type: object) -> str:
    value = getattr(provider_type, "value", provider_type)
    return str(value)


def _enkripsi(plaintext: str) -> bytes:
    try:
        return encrypt_credential(plaintext)
    except CredentialError as exc:
        raise ValidationError(str(exc), field="credential") from None


def _wajib_ada(db: Session, product_id: uuid.UUID) -> AiProduct:
    product = repository.get_by_id(db, product_id)
    if product is None:
        raise NotFoundError("Produk AI tidak ditemukan")
    return product


def _pastikan_nama_bebas(db: Session, name: str, *, kecuali: uuid.UUID | None = None) -> None:
    ada = repository.get_by_name(db, name)
    if ada is not None and ada.id != kecuali:
        raise ConflictError(f"Nama produk AI '{name}' sudah dipakai", code=NAME_TAKEN)


# Semua isian khusus jenis (Otomatis dan Manual), untuk aturan per jenis SCRUM-201.
_SEMUA_ISIAN_JENIS = tuple(f for jenis in MeasurementType for f in rules.TYPE_FIELDS[jenis])


def _pastikan_isian_sesuai_jenis(jenis: MeasurementType, isian: dict) -> None:
    """422 FIELD_REQUIRED / FIELD_NOT_ALLOWED bila isian tidak sesuai jenis (AC1-AC3)."""
    masalah = rules.type_field_problem(
        jenis, isian, access_note_required=get_settings().ai_product_access_note_required
    )
    if masalah is not None:
        raise ValidationError(masalah.message, code=masalah.code, field=masalah.field)


def _isian_setelah_ubah(
    product: AiProduct, perubahan: dict, credential: str | None, *, berganti: bool
) -> dict:
    """Nilai akhir tiap isian jenis setelah PATCH, sebelum produk benar-benar diubah.

    Saat jenis berganti, isian jenis lama dikosongkan (D1 C2), jadi hanya isian
    di body yang dihitung. Kredensial lama tetap berlaku selama jenisnya sama.
    """

    def nilai(field: str):
        if field == "credential":
            if credential is not None:
                return credential
            return None if berganti else product.credential_encrypted
        if field in perubahan:
            return perubahan[field]
        return None if berganti else getattr(product, field)

    return {field: nilai(field) for field in _SEMUA_ISIAN_JENIS}


def _label_penyedia(product: AiProduct) -> str | None:
    """vendor_name untuk produk Manual, label provider_type untuk produk Otomatis (D1)."""
    if product.measurement_type == MeasurementType.MANUAL:
        return product.vendor_name
    return _LABEL_PROVIDER.get(product.provider_type)


def _tampilkan(product: AiProduct) -> AiProductRead:
    return AiProductRead(
        id=product.id,
        name=product.name,
        measurement_type=product.measurement_type,
        provider_type=product.provider_type,
        base_url=product.base_url,
        model_name=product.model_name,
        credential_hint=product.credential_hint,
        has_credential=product.credential_encrypted is not None,
        rate_limit_per_minute=product.rate_limit_per_minute,
        monthly_budget_idr=product.monthly_budget_idr,
        model_params=product.model_params,
        price_input_usd_per_mtok=product.price_input_usd_per_mtok,
        price_output_usd_per_mtok=product.price_output_usd_per_mtok,
        vendor_name=product.vendor_name,
        vendor_label=_label_penyedia(product),
        site_url=product.site_url,
        access_note=product.access_note,
        manual_params=product.manual_params,
        is_active=product.is_active,
        last_test_at=product.last_test_at,
        last_test_status=product.last_test_status,
        last_test_message=product.last_test_message,
        last_test_error_category=product.last_test_error_category,
        created_by=product.created_by,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )
