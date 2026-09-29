"""Logika bisnis modul cases.

PBI-3, SCRUM-106. Ini satu-satunya pintu masuk yang boleh dipanggil
modul lain. Service tidak boleh menyentuh HTTP. Aturan isian ada di
validation.py; di sini yang mengunci suite aktif, kode unik, dan siapa
yang boleh mengubah kasus.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.cases import completeness, repository, validation
from app.modules.cases.models import Case, CaseStatus, SplitTag
from app.modules.cases.schemas import CaseCompleteness, CaseRead, CaseSummary, CaseWrite
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError

CASE_CODE_TAKEN = "CASE_CODE_TAKEN"
SUITE_NOT_ACTIVE = "SUITE_NOT_ACTIVE"
SPLIT_TAG_LOCKED = "SPLIT_TAG_LOCKED"


def create_case(
    db: Session, suite_id: uuid.UUID, payload: CaseWrite, *, actor_id: uuid.UUID
) -> CaseRead:
    """Store a new case as draft. An inactive suite is rejected."""
    _require_active_suite(db, suite_id)
    _require_free_code(db, payload.case_code)
    case = Case(
        suite_id=suite_id,
        status=CaseStatus.DRAFT,
        version=1,
        created_by=actor_id,
        updated_by=actor_id,
    )
    _copy_to_case(case, payload, actor_id=actor_id)
    try:
        tersimpan = repository.create(db, case)
    except IntegrityError:
        db.rollback()
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan)


def get_case(db: Session, case_id: uuid.UUID) -> CaseRead:
    """Return one case, or raise when the id does not exist."""
    return _to_read(_require_case(db, case_id))


def get_completeness(db: Session, case_id: uuid.UUID) -> CaseCompleteness:
    """Kelengkapan satu kasus, dihitung ulang dari baris yang tersimpan.

    Dihitung dari kolom isi, bukan dari kolom `completeness`, supaya baris
    lama yang disimpan sebelum formula ini tetap menjawab dengan benar.
    """
    case = _require_case(db, case_id)
    return CaseCompleteness(**completeness.from_row(case))


def list_cases(
    db: Session,
    suite_id: uuid.UUID,
    *,
    status: CaseStatus | None = None,
    split_tag: SplitTag | None = None,
) -> list[CaseSummary]:
    """Return the short list for a suite that exists. Filters are optional."""
    _require_suite(db, suite_id)
    baris = repository.list_for_suite(db, suite_id, status=status, split_tag=split_tag)
    return [_to_summary(item) for item in baris]


def update_case(
    db: Session,
    case_id: uuid.UUID,
    payload: CaseWrite,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> CaseRead:
    """Update a case. Only the creator or an admin may do so.

    An archived suite is rejected with SUITE_NOT_ACTIVE, same as create.
    Before approval, only the creator may change split_tag.

    TODO: an approved case stays approved when its content changes. The review
    flow should lock that edit or send the case back.
    """
    case = _require_case(db, case_id)
    _require_active_suite(db, case.suite_id)
    _require_can_update(case, payload, actor_id=actor_id, is_admin=is_admin)
    if payload.case_code != case.case_code:
        _require_free_code(db, payload.case_code)
    _copy_to_case(case, payload, actor_id=actor_id)
    case.version += 1
    try:
        tersimpan = repository.save(db, case)
    except IntegrityError:
        db.rollback()
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan)


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count cases in one suite. Called by the suites module."""
    return repository.count_for_suite(db, suite_id)


def has_approved_case(db: Session, suite_id: uuid.UUID) -> bool:
    """True when the suite contains a case whose status is approved."""
    return repository.has_approved(db, suite_id)


def _copy_to_case(case: Case, payload: CaseWrite, *, actor_id: uuid.UUID) -> None:
    """Copy the write body onto the row and store the completeness indicator."""
    data = payload.model_dump(mode="json")
    case.case_code = payload.case_code
    case.title = payload.identity.title
    case.question = payload.identity.question
    case.category = payload.identity.category
    case.legal_refs = data["legal_refs"]
    case.answer_criteria = data["answer_criteria"]
    case.traps = data["traps"]
    case.split_tag = payload.split_tag
    case.completeness = validation.completeness(data)
    case.updated_by = actor_id


def _to_read(case: Case) -> CaseRead:
    """Build the full read model from a row. Status stays server-owned."""
    return CaseRead(
        id=case.id,
        suite_id=case.suite_id,
        case_code=case.case_code,
        identity={
            "title": case.title,
            "question": case.question,
            "category": case.category,
        },
        legal_refs=case.legal_refs or [],
        answer_criteria=case.answer_criteria or {},
        traps=case.traps or [],
        split_tag=case.split_tag,
        status=case.status,
        completeness_pct=_completeness_pct(case),
        version=case.version,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _to_summary(case: Case) -> CaseSummary:
    """Build the short list item. The full body is not included."""
    return CaseSummary(
        id=case.id,
        case_code=case.case_code,
        title=case.title,
        split_tag=case.split_tag,
        status=case.status,
        completeness_pct=_completeness_pct(case),
        updated_at=case.updated_at,
    )


def _completeness_pct(case: Case) -> int:
    """Read completeness.pct, or 0 when the stored value is missing or invalid."""
    mentah = case.completeness or {}
    try:
        return int(mentah.get("pct", 0))
    except (TypeError, ValueError):
        return 0


def _require_case(db: Session, case_id: uuid.UUID) -> Case:
    """Load a case or raise NotFoundError."""
    case = repository.get_by_id(db, case_id)
    if case is None:
        raise NotFoundError("Kasus tidak ditemukan")
    return case


def _require_suite(db: Session, suite_id: uuid.UUID):
    """Load the suite through suites.service.

    The import stays inside the function so suites.service can import this
    module at import time without a cycle.
    """
    from app.modules.suites import service as suites_service

    return suites_service.get_suite(db, suite_id)


def _require_active_suite(db: Session, suite_id: uuid.UUID) -> None:
    """Reject writes when the suite is missing or not active."""
    suite = _require_suite(db, suite_id)
    if suite.status != "active":
        raise ValidationError(
            "Kasus hanya bisa ditulis di suite yang aktif",
            code=SUITE_NOT_ACTIVE,
        )


def _require_free_code(db: Session, case_code: str) -> None:
    """Reject a case_code that another suite already owns."""
    if repository.get_by_code(db, case_code) is not None:
        raise _code_taken(db, case_code)


def _code_taken(db: Session, case_code: str) -> ConflictError:
    """Build CASE_CODE_TAKEN. The message names the suite that owns the code."""
    pemilik = repository.get_by_code(db, case_code)
    nama = "suite lain"
    if pemilik is not None:
        nama = _suite_name(db, pemilik.suite_id)
    return ConflictError(
        f"Kode kasus '{case_code}' sudah dipakai di suite '{nama}'",
        code=CASE_CODE_TAKEN,
    )


def _suite_name(db: Session, suite_id: uuid.UUID) -> str:
    """Return the suite name, or a fallback when that suite is already gone."""
    from app.modules.suites import service as suites_service

    try:
        return suites_service.get_suite(db, suite_id).name
    except NotFoundError:
        return "suite lain"


def _require_can_update(
    case: Case, payload: CaseWrite, *, actor_id: uuid.UUID, is_admin: bool
) -> None:
    """Allow the creator or an admin. Lock split_tag for everyone else before approval."""
    pembuat = case.created_by == actor_id
    if not pembuat and not is_admin:
        raise ForbiddenError("Hanya pembuat kasus atau admin yang boleh mengubah kasus ini")
    if payload.split_tag != case.split_tag and case.status != CaseStatus.APPROVED and not pembuat:
        raise ForbiddenError(
            "Sebelum kasus disetujui, hanya pembuat yang boleh mengubah split_tag",
            code=SPLIT_TAG_LOCKED,
        )
