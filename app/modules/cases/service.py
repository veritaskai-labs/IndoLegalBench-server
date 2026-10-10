"""Logika bisnis modul cases.

PBI-3, SCRUM-106. SCRUM-136 memindahkan isi kasus ke case_versions.
Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Aturan isian ada di validation.py; di sini
yang mengunci suite aktif, kode unik, versi yang disetujui, dan siapa
yang boleh mengubah kasus.
"""

import copy
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.cases import completeness, repository, sections, validation
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.cases.schemas import (
    ActorRead,
    CaseCompleteness,
    CaseRead,
    CaseSummary,
    CaseWrite,
    VersionCompare,
    VersionSections,
    VersionSide,
    VersionSummary,
)
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError

CASE_CODE_TAKEN = "CASE_CODE_TAKEN"
CASE_CODE_LOCKED = "CASE_CODE_LOCKED"
SUITE_NOT_ACTIVE = "SUITE_NOT_ACTIVE"
SPLIT_TAG_LOCKED = "SPLIT_TAG_LOCKED"
VERSION_LOCKED = "VERSION_LOCKED"
VERSION_IN_PROGRESS = "VERSION_IN_PROGRESS"
NO_APPROVED_VERSION = "NO_APPROVED_VERSION"

_EDITABLE = frozenset({CaseStatus.DRAFT, CaseStatus.NEEDS_REVISION})
_IN_PROGRESS = frozenset({CaseStatus.DRAFT, CaseStatus.IN_REVIEW, CaseStatus.NEEDS_REVISION})


def create_case(
    db: Session, suite_id: uuid.UUID, payload: CaseWrite, *, actor_id: uuid.UUID
) -> CaseRead:
    """Store a new case as draft version 1. An inactive suite is rejected."""
    _require_active_suite(db, suite_id)
    _require_free_code(db, payload.case_code)
    case_id = uuid.uuid4()
    version_id = uuid.uuid4()
    case = Case(
        id=case_id,
        suite_id=suite_id,
        case_code=payload.case_code,
        current_version_id=version_id,
        latest_approved_version_id=None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    version = CaseVersion(
        id=version_id,
        case_id=case_id,
        version_no=1,
        status=CaseStatus.DRAFT,
        case_code=payload.case_code,
        split_tag=payload.split_tag,
        content={},
        created_by=actor_id,
        based_on_version_id=None,
    )
    _write_version(case, version, payload, actor_id=actor_id)
    _catat_versi_baru(version)
    try:
        tersimpan = repository.create(db, case, version)
    except IntegrityError:
        db.rollback()
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan, version)


def get_case(db: Session, case_id: uuid.UUID) -> CaseRead:
    """Return the version that is in effect, or raise when the id does not exist."""
    return _to_read(_require_case(db, case_id))


def get_completeness(db: Session, case_id: uuid.UUID) -> CaseCompleteness:
    """Kelengkapan versi yang sedang dikerjakan, dihitung ulang dari isinya.

    Dihitung dari content versi saat ini, bukan dari salinan completeness
    yang tersimpan, supaya baris lama tetap menjawab dengan formula terbaru.
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
    """Return the short list for a suite that exists. Filters use the in-effect version."""
    _require_suite(db, suite_id)
    baris = repository.list_for_suite(db, suite_id, status=status, split_tag=split_tag)
    return [_to_summary(case, version) for case, version in baris]


def update_case(
    db: Session,
    case_id: uuid.UUID,
    payload: CaseWrite,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> CaseRead:
    """Update the open version in place.

    Only a draft or needs_revision version can be written. An approved or
    in-review version is rejected with VERSION_LOCKED. An archived suite is
    rejected with SUITE_NOT_ACTIVE, same as create.
    """
    case = _require_case(db, case_id)
    _require_active_suite(db, case.suite_id)
    version = case.current_version
    _require_editable(version)
    _require_can_update(case, version, payload, actor_id=actor_id, is_admin=is_admin)
    if payload.case_code != case.case_code:
        _require_free_code(db, payload.case_code)
    _write_version(case, version, payload, actor_id=actor_id)
    try:
        tersimpan = repository.save(db, case)
    except IntegrityError:
        db.rollback()
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan, version)


def start_new_version(
    db: Session,
    case_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> CaseRead:
    """Copy the approved wording into a new draft.

    The approved version stays the one in effect. The creator or an admin
    may do this. A case with no approved version, or with a version already
    in progress, is rejected.
    """
    case = _require_case(db, case_id)
    _require_active_suite(db, case.suite_id)
    _require_creator_or_admin(case, actor_id=actor_id, is_admin=is_admin)
    approved = case.latest_approved_version
    if approved is None:
        raise ConflictError(
            "Kasus ini belum punya versi yang disetujui",
            code=NO_APPROVED_VERSION,
        )
    if case.current_version.status in _IN_PROGRESS:
        raise ConflictError(
            "Sudah ada versi yang masih berupa draf atau sedang ditinjau",
            code=VERSION_IN_PROGRESS,
        )
    version_id = uuid.uuid4()
    version = CaseVersion(
        id=version_id,
        case_id=case.id,
        version_no=repository.max_version_no(db, case.id) + 1,
        status=CaseStatus.DRAFT,
        case_code=approved.case_code,
        split_tag=approved.split_tag,
        content=copy.deepcopy(approved.content or {}),
        created_by=actor_id,
        based_on_version_id=approved.id,
    )
    case.current_version_id = version_id
    case.updated_by = actor_id
    _catat_versi_baru(version)
    try:
        repository.create(db, case, version)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "Sudah ada versi yang masih berupa draf atau sedang ditinjau",
            code=VERSION_IN_PROGRESS,
        ) from None
    return _to_read(case, version)


def list_case_versions(db: Session, case_id: uuid.UUID) -> list[VersionSummary]:
    """Every version, with the sections that differ from the previous one.

    Version 1 has an empty changed list. The diff is computed here, not stored.
    """
    case = _require_case(db, case_id)
    versions = repository.list_versions(db, case.id)
    names = auth_service.user_names(db, {version.created_by for version in versions})
    previous: dict | None = None
    rows: list[VersionSummary] = []
    for version in versions:
        current = _section_values(version)
        changed = [] if previous is None else sections.changed_sections(previous, current)
        rows.append(
            VersionSummary(
                version_no=version.version_no,
                status=version.status,
                author=_author(version.created_by, names),
                created_at=version.created_at,
                changed=changed,
            )
        )
        previous = current
    return rows


def compare_case_versions(db: Session, case_id: uuid.UUID, a: int, b: int) -> VersionCompare:
    """Diff version numbers a and b of one case.

    The same number compared with itself has an empty changed list. A number
    that is not on this case is a 404.
    """
    case = _require_case(db, case_id)
    left = _require_version(db, case.id, a)
    right = _require_version(db, case.id, b)
    names = auth_service.user_names(db, {left.created_by, right.created_by})
    left_sections = _section_values(left)
    right_sections = _section_values(right)
    changed = [] if a == b else sections.changed_sections(left_sections, right_sections)
    return VersionCompare(
        a=_version_side(left, names, left_sections),
        b=_version_side(right, names, right_sections),
        changed=changed,
    )


def approved_copies_for_suite(db: Session, suite_id: uuid.UUID) -> list[dict]:
    """Frozen bodies of each case's latest approved version.

    Draft-only cases are left out.
    """
    copies: list[dict] = []
    for case, version in repository.approved_for_suite(db, suite_id):
        copies.append(
            {
                "case_id": case.id,
                "case_version_id": version.id,
                "body": sections.snapshot_body(
                    case_code=version.case_code,
                    version_no=version.version_no,
                    status=version.status.value,
                    split_tag=version.split_tag.value,
                    content=version.content,
                ),
            }
        )
    return copies


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count cases in one suite. Called by the suites module."""
    return repository.count_for_suite(db, suite_id)


def has_approved_case(db: Session, suite_id: uuid.UUID) -> bool:
    """True when the suite contains a case that has an approved version."""
    return repository.has_approved(db, suite_id)


def _version_side(version: CaseVersion, names: dict[uuid.UUID, str], values: dict) -> VersionSide:
    return VersionSide(
        version_no=version.version_no,
        status=version.status,
        author=_author(version.created_by, names),
        created_at=version.created_at,
        sections=VersionSections.model_validate(values),
    )


def _author(user_id: uuid.UUID, names: dict[uuid.UUID, str]) -> ActorRead:
    """Id plus display name. A missing user row leaves the name empty."""
    return ActorRead(id=user_id, name=names.get(user_id, ""))


def _section_values(version: CaseVersion) -> dict:
    """The eight sections for one version, including its stored case_code."""
    return sections.sections_from(
        case_code=version.case_code,
        split_tag=version.split_tag.value,
        content=version.content,
    )


def _catat_versi_baru(version: CaseVersion) -> None:
    """TODO(SCRUM-140): record the new-version event in audit_logs.

    The audit table and listener belong to that ticket. This call site is
    the only place a new case_versions row is inserted.
    """
    del version


def _write_version(
    case: Case, version: CaseVersion, payload: CaseWrite, *, actor_id: uuid.UUID
) -> None:
    """Copy the write body onto the version and store the completeness indicator."""
    data = payload.model_dump(mode="json")
    case.case_code = payload.case_code
    case.updated_by = actor_id
    version.case_code = payload.case_code
    version.split_tag = payload.split_tag
    version.content = {
        "title": payload.identity.title,
        "question": payload.identity.question,
        "category": payload.identity.category,
        "legal_refs": data["legal_refs"],
        "answer_criteria": data["answer_criteria"],
        "traps": data["traps"],
        "completeness": validation.completeness(data),
    }


def _to_read(case: Case, version: CaseVersion | None = None) -> CaseRead:
    """Build the full read model. Status stays server-owned.

    Without an explicit version, the response is the version in effect.
    """
    versi = version if version is not None else _in_effect(case)
    isi = versi.content or {}
    return CaseRead(
        id=case.id,
        suite_id=case.suite_id,
        case_code=case.case_code,
        identity={
            "title": isi.get("title") or "",
            "question": isi.get("question") or "",
            "category": isi.get("category"),
        },
        legal_refs=isi.get("legal_refs") or [],
        answer_criteria=isi.get("answer_criteria") or {},
        traps=isi.get("traps") or [],
        split_tag=versi.split_tag,
        status=versi.status,
        completeness_pct=_completeness_pct(isi.get("completeness")),
        version=versi.version_no,
        created_at=versi.created_at,
        updated_at=versi.updated_at,
    )


def _to_summary(case: Case, version: CaseVersion) -> CaseSummary:
    """Build the short list item from the version that is in effect.

    Completeness is recalculated. A stored pct can be stale after the
    formula changes.
    """
    isi = version.content or {}
    return CaseSummary(
        id=case.id,
        case_code=case.case_code,
        title=isi.get("title") or "",
        split_tag=version.split_tag,
        status=version.status,
        completeness_pct=_pct_from_version(case, version),
        updated_at=version.updated_at,
    )


def _in_effect(case: Case) -> CaseVersion:
    """The approved wording when one exists, otherwise the only open version."""
    if case.latest_approved_version is not None:
        return case.latest_approved_version
    return case.current_version


def _pct_from_version(case: Case, version: CaseVersion) -> int:
    """Recalculate completeness from the version being shown."""
    isi = version.content or {}
    tag = version.split_tag
    return int(
        completeness.evaluate(
            {
                "case_code": case.case_code,
                "identity": {"title": isi.get("title"), "question": isi.get("question")},
                "legal_refs": isi.get("legal_refs") or [],
                "answer_criteria": isi.get("answer_criteria") or {},
                "traps": isi.get("traps") or [],
                "split_tag": str(tag) if tag else None,
            }
        )["pct"]
    )


def _completeness_pct(mentah: dict | None) -> int:
    """Read completeness.pct, or 0 when the stored value is missing or invalid."""
    if not isinstance(mentah, dict):
        return 0
    try:
        return int(mentah.get("pct", 0))
    except (TypeError, ValueError):
        return 0


def _require_version(db: Session, case_id: uuid.UUID, version_no: int) -> CaseVersion:
    """Load one version of this case, or raise NotFoundError."""
    version = repository.get_version(db, case_id, version_no)
    if version is None:
        raise NotFoundError("Versi tidak ditemukan")
    return version


def _require_case(db: Session, case_id: uuid.UUID) -> Case:
    """Load a case or raise NotFoundError."""
    case = repository.get_by_id(db, case_id)
    if case is None:
        raise NotFoundError("Kasus tidak ditemukan")
    return case


def _require_editable(version: CaseVersion) -> None:
    """Reject a write against an approved or in-review version."""
    if version.status in _EDITABLE:
        return
    if version.status == CaseStatus.APPROVED:
        raise ConflictError(
            "Versi yang sudah disetujui tidak bisa diubah. Buat versi baru.",
            code=VERSION_LOCKED,
        )
    raise ConflictError(
        "Versi yang sedang ditinjau tidak bisa diubah.",
        code=VERSION_LOCKED,
    )


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


def _require_creator_or_admin(case: Case, *, actor_id: uuid.UUID, is_admin: bool) -> None:
    """Allow the case creator or an admin."""
    if case.created_by == actor_id or is_admin:
        return
    raise ForbiddenError("Hanya pembuat kasus atau admin yang boleh mengubah kasus ini")


def _require_can_update(
    case: Case,
    version: CaseVersion,
    payload: CaseWrite,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> None:
    """Allow the creator or an admin. Lock split_tag until the case has been approved.

    Once a version is approved, case_code stays on the shared case row. A
    draft must not rename it, or GET would show the approved wording under
    the new code before the draft is reviewed.
    """
    _require_creator_or_admin(case, actor_id=actor_id, is_admin=is_admin)
    belum_disetujui = case.latest_approved_version_id is None
    if payload.case_code != case.case_code and not belum_disetujui:
        raise ConflictError(
            "Kode kasus tidak bisa diubah setelah ada versi yang disetujui",
            code=CASE_CODE_LOCKED,
        )
    if payload.split_tag != version.split_tag and belum_disetujui and case.created_by != actor_id:
        raise ForbiddenError(
            "Sebelum kasus disetujui, hanya pembuat yang boleh mengubah split_tag",
            code=SPLIT_TAG_LOCKED,
        )
