"""Logika bisnis provider penilai (LLM judge). PBI-14 AC1, AC6, SCRUM-258.

Terpisah dari service produk AI supaya penilai tidak pernah masuk pilihan produk,
dashboard, atau laporan (catatan SA 6 Nov), tetapi memakai ulang enkripsi dan
masking kredensial yang sama (crypto.py). Service tidak menyentuh HTTP.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.providers import judge_repository
from app.modules.providers.adapters.dispatch import build_adapter
from app.modules.providers.adapters.safe import SafeProviderAdapter
from app.modules.providers.crypto import (
    CredentialError,
    credential_hint,
    decrypt_credential,
    encrypt_credential,
)
from app.modules.providers.judge_schemas import (
    JudgeProviderCreate,
    JudgeProviderOption,
    JudgeProviderRead,
    JudgeProviderUpdate,
)
from app.modules.providers.models import AiProduct, JudgeProvider, MeasurementType
from app.shared.exceptions import ConflictError, InternalError, NotFoundError, ValidationError

NAME_TAKEN = "JUDGE_PROVIDER_NAME_TAKEN"
NOT_FOUND = "JUDGE_PROVIDER_NOT_FOUND"
_UNIQUE_NAME = "uq_judge_providers_name"
_CREDENTIAL = "credential"


def create_judge(
    db: Session, payload: JudgeProviderCreate, *, created_by: uuid.UUID
) -> JudgeProviderRead:
    _pastikan_nama_bebas(db, payload.name)
    judge = JudgeProvider(
        **payload.model_dump(exclude={_CREDENTIAL}),
        created_by=created_by,
    )
    _isi_kredensial(judge, payload.credential)
    try:
        tersimpan = judge_repository.create(db, judge)
    except IntegrityError as exc:
        db.rollback()
        _terjemahkan_bentrok_nama(exc, payload.name)
    return _tampilkan(tersimpan)


def list_judges(db: Session) -> list[JudgeProviderRead]:
    return [_tampilkan(judge) for judge in judge_repository.list_all(db)]


def get_judge(db: Session, judge_id: uuid.UUID) -> JudgeProviderRead:
    return _tampilkan(_wajib_ada(db, judge_id))


def list_options(db: Session) -> list[JudgeProviderOption]:
    """Penilai untuk dropdown form pengukuran: produk AI otomatis yang aktif (SCRUM-271)."""
    return [
        JudgeProviderOption(
            id=row.id, name=row.name, provider_type=row.provider_type, model_name=row.model_name
        )
        for row in judge_repository.list_product_options(db)
    ]


def update_judge(
    db: Session, judge_id: uuid.UUID, payload: JudgeProviderUpdate
) -> JudgeProviderRead:
    judge = _wajib_ada(db, judge_id)
    perubahan = payload.model_dump(exclude_unset=True)
    credential = perubahan.pop(_CREDENTIAL, None)
    for kolom, value in perubahan.items():
        setattr(judge, kolom, value)
    if credential is not None:
        _isi_kredensial(judge, credential)
    return _tampilkan(judge_repository.save(db, judge))


def set_active(db: Session, judge_id: uuid.UUID, *, is_active: bool) -> JudgeProviderRead:
    judge = _wajib_ada(db, judge_id)
    judge.is_active = is_active
    return _tampilkan(judge_repository.save(db, judge))


@dataclass(frozen=True)
class JudgeBinding:
    """Penilai siap pakai untuk judge.run_judge (SCRUM-224).

    asker sudah dibungkus SafeProviderAdapter. Harga dan anggaran bulanan milik produk AI
    yang dipilih sebagai penilai, tidak ada anggaran penilai terpisah (SCRUM-271). Harga bisa
    kosong di produknya, tetapi penilaian menolak penilai tanpa harga lengkap sebelum
    memanggilnya (JudgePricingMissing). Kurs harian disusun pemanggil menjadi JudgePrices. is_active dilaporkan,
    tidak ditolak di sini.
    """

    judge_provider_id: uuid.UUID
    asker: SafeProviderAdapter = field(repr=False)
    price_input_usd_per_mtok: Decimal | None
    price_output_usd_per_mtok: Decimal | None
    monthly_budget_idr: Decimal
    is_active: bool


def build_judge(db: Session, judge_id: uuid.UUID) -> JudgeBinding:
    """Adapter penilai dari produk AI otomatis yang dipilih. Kredensial hanya dibuka di sini."""
    produk = _produk_penilai(db, judge_id)
    try:
        secret = decrypt_credential(produk.credential_encrypted)
    except CredentialError:
        raise InternalError("judge credential unavailable") from None
    adapter = build_adapter(
        provider_type=produk.provider_type.value,
        base_url=produk.base_url,
        model_name=produk.model_name,
        api_key=secret,
    )
    return JudgeBinding(
        judge_provider_id=produk.id,
        asker=SafeProviderAdapter(adapter, secret, produk.base_url),
        price_input_usd_per_mtok=produk.price_input_usd_per_mtok,
        price_output_usd_per_mtok=produk.price_output_usd_per_mtok,
        monthly_budget_idr=produk.monthly_budget_idr,
        is_active=produk.is_active,
    )


@dataclass(frozen=True)
class JudgePricing:
    """Harga token dan batas bulanan produk penilai, tanpa adapter dan tanpa kredensial.

    Untuk perkiraan biaya (SCRUM-286): bentuknya cocok dengan yang dibaca open_judge_meter dari
    JudgeBinding, jadi harga dan anggaran yang dihitung sama dengan penilaian sungguhan.
    """

    judge_provider_id: uuid.UUID
    provider_type: str
    price_input_usd_per_mtok: Decimal | None
    price_output_usd_per_mtok: Decimal | None
    monthly_budget_idr: Decimal


def judge_pricing(db: Session, judge_id: uuid.UUID) -> JudgePricing:
    """Harga dan anggaran produk penilai. Kredensial tidak pernah dibuka (beda dengan build_judge).

    Produk yang sah sama dengan build_judge: hanya produk AI otomatis. Aktif atau tidaknya
    dicek saat memilih judge (is_eligible_judge), bukan di sini.
    """
    produk = _produk_penilai(db, judge_id)
    return JudgePricing(
        judge_provider_id=produk.id,
        provider_type=produk.provider_type.value,
        price_input_usd_per_mtok=produk.price_input_usd_per_mtok,
        price_output_usd_per_mtok=produk.price_output_usd_per_mtok,
        monthly_budget_idr=produk.monthly_budget_idr,
    )


def judge_names(db: Session, judge_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Nama penilai untuk kolom Objek audit log (PBI-18 F-06), termasuk yang nonaktif."""
    if not judge_ids:
        return {}
    return judge_repository.names_by_ids(db, judge_ids)


def lock_available_for_new_session(db: Session, judge_id: uuid.UUID) -> bool:
    """Kunci baris produk penilai (FOR SHARE) untuk sesi yang akan dibuat, lalu layak dan aktifnya.

    Untuk POST /measurements (SCRUM-258, SCRUM-271): kunci bertahan sampai transaksi pembuat
    sesi selesai, jadi nonaktifkan yang belum commit ditunggu dan hasilnya terbaca, seperti
    kunci produk #90. Hanya produk otomatis yang belum dihapus yang bisa menjadi penilai.
    """
    produk = judge_repository.get_product(db, judge_id, for_share=True)
    return _bisa_jadi_penilai(produk) and produk.is_active


def is_eligible_judge(db: Session, judge_id: uuid.UUID) -> bool:
    """Judge yang boleh dipilih Admin saat menilai: produk otomatis yang aktif (SCRUM-280)."""
    produk = judge_repository.get_product(db, judge_id)
    return _bisa_jadi_penilai(produk) and bool(produk.is_active)


def _bisa_jadi_penilai(produk: AiProduct | None) -> bool:
    return produk is not None and produk.measurement_type == MeasurementType.AUTOMATIC


def _produk_penilai(db: Session, judge_id: uuid.UUID) -> AiProduct:
    produk = judge_repository.get_product(db, judge_id)
    if not _bisa_jadi_penilai(produk):
        raise NotFoundError("Provider penilai tidak ditemukan", code=NOT_FOUND)
    return produk


def _wajib_ada(db: Session, judge_id: uuid.UUID) -> JudgeProvider:
    judge = judge_repository.get_by_id(db, judge_id)
    if judge is None:
        raise NotFoundError("Provider penilai tidak ditemukan", code=NOT_FOUND)
    return judge


def _pastikan_nama_bebas(db: Session, name: str) -> None:
    if judge_repository.get_by_name(db, name) is not None:
        raise _nama_terpakai(name)


def _terjemahkan_bentrok_nama(exc: IntegrityError, name: str) -> NoReturn:
    """Hanya unik nama yang menjadi 409; FK atau constraint lain tidak disamarkan."""
    pesan = str(exc.orig)
    if _UNIQUE_NAME in pesan or "judge_providers.name" in pesan:
        raise _nama_terpakai(name) from None
    raise exc


def _nama_terpakai(name: str) -> ConflictError:
    return ConflictError(f"Nama provider penilai '{name}' sudah dipakai", code=NAME_TAKEN)


def _isi_kredensial(judge: JudgeProvider, plaintext: str) -> None:
    try:
        judge.credential_encrypted = encrypt_credential(plaintext)
    except CredentialError as exc:
        raise ValidationError(str(exc), field=_CREDENTIAL) from None
    judge.credential_hint = credential_hint(plaintext)


def _tampilkan(judge: JudgeProvider) -> JudgeProviderRead:
    return JudgeProviderRead(
        id=judge.id,
        name=judge.name,
        provider_type=judge.provider_type,
        base_url=judge.base_url,
        model_name=judge.model_name,
        credential_hint=judge.credential_hint,
        has_credential=judge.credential_encrypted is not None,
        price_input_usd_per_mtok=judge.price_input_usd_per_mtok,
        price_output_usd_per_mtok=judge.price_output_usd_per_mtok,
        monthly_budget_idr=judge.monthly_budget_idr,
        is_active=judge.is_active,
        created_by=judge.created_by,
        created_at=judge.created_at,
        updated_at=judge.updated_at,
    )
