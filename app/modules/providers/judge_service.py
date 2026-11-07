"""Logika bisnis provider penilai (LLM judge). PBI-14 AC1, AC6, SCRUM-258.

Terpisah dari service produk AI supaya penilai tidak pernah masuk pilihan produk,
dashboard, atau laporan (catatan SA 6 Nov), tetapi memakai ulang enkripsi dan
masking kredensial yang sama (crypto.py). Service tidak menyentuh HTTP.
"""

import uuid
from typing import NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.providers import judge_repository
from app.modules.providers.crypto import CredentialError, credential_hint, encrypt_credential
from app.modules.providers.judge_schemas import (
    JudgeProviderCreate,
    JudgeProviderOption,
    JudgeProviderRead,
    JudgeProviderUpdate,
)
from app.modules.providers.models import JudgeProvider
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError

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
    """Penilai aktif untuk dropdown form pengukuran (SCRUM-211)."""
    return [
        JudgeProviderOption(
            id=row.id, name=row.name, provider_type=row.provider_type, model_name=row.model_name
        )
        for row in judge_repository.list_options(db)
    ]


def update_judge(
    db: Session, judge_id: uuid.UUID, payload: JudgeProviderUpdate
) -> JudgeProviderRead:
    judge = _wajib_ada(db, judge_id)
    perubahan = payload.model_dump(exclude_unset=True)
    credential = perubahan.pop(_CREDENTIAL, None)
    for field, value in perubahan.items():
        setattr(judge, field, value)
    if credential is not None:
        _isi_kredensial(judge, credential)
    return _tampilkan(judge_repository.save(db, judge))


def set_active(db: Session, judge_id: uuid.UUID, *, is_active: bool) -> JudgeProviderRead:
    judge = _wajib_ada(db, judge_id)
    judge.is_active = is_active
    return _tampilkan(judge_repository.save(db, judge))


def judge_names(db: Session, judge_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Nama penilai untuk kolom Objek audit log (PBI-18 F-06), termasuk yang nonaktif."""
    if not judge_ids:
        return {}
    return judge_repository.names_by_ids(db, judge_ids)


def lock_available_for_new_session(db: Session, judge_id: uuid.UUID) -> bool:
    """Kunci baris penilai (FOR SHARE) untuk sesi yang akan dibuat, lalu ada dan aktifnya.

    Untuk POST /measurements (SCRUM-258): kunci bertahan sampai transaksi pembuat sesi
    selesai, jadi nonaktifkan yang belum commit ditunggu dan hasilnya terbaca, seperti
    kunci produk #90.
    """
    judge = judge_repository.get_by_id(db, judge_id, for_share=True)
    return judge is not None and judge.is_active


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
