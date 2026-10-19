"""Logika bisnis modul cases.

PBI-3, SCRUM-106. SCRUM-136 memindahkan isi kasus ke case_versions.
Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Aturan isian ada di validation.py; di sini
yang mengunci suite aktif, kode unik, versi yang disetujui, dan siapa
yang boleh mengubah kasus.
"""

import copy
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.cases import (
    completeness,
    legal_ref_preview,
    repository,
    sections,
    validation,
)
from app.modules.cases.display_text import citation_text
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.cases.schemas import (
    ActorRead,
    CaseCompleteness,
    CaseRead,
    CaseSummary,
    CaseWrite,
    LegalRefPreviewItem,
    LegalRefRead,
    LegalRefStored,
    ReviewCopy,
    ReviewSubmission,
    VersionCompare,
    VersionSections,
    VersionSide,
    VersionSummary,
)
from app.modules.legal_refs import service as legal_refs_service
from app.modules.notifications.service import DbNotifier
from app.modules.reviews import service as reviews_service
from app.modules.reviews.ports import DeferredNotifier
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError

CASE_CODE_TAKEN = "CASE_CODE_TAKEN"
CASE_CODE_LOCKED = "CASE_CODE_LOCKED"
SUITE_NOT_ACTIVE = "SUITE_NOT_ACTIVE"
SPLIT_TAG_LOCKED = "SPLIT_TAG_LOCKED"
VERSION_LOCKED = "VERSION_LOCKED"
VERSION_IN_PROGRESS = "VERSION_IN_PROGRESS"
NO_APPROVED_VERSION = "NO_APPROVED_VERSION"
CASE_NOT_READY = "CASE_NOT_READY"
VERSION_NOT_SUBMITTABLE = "VERSION_NOT_SUBMITTABLE"
NO_OPEN_VERSION = "NO_OPEN_VERSION"
VERSION_NOT_IN_REVIEW = "VERSION_NOT_IN_REVIEW"
TAG_CHANGE_NEEDS_CONFIRMATION = "TAG_CHANGE_NEEDS_CONFIRMATION"

_EDITABLE = frozenset({CaseStatus.DRAFT, CaseStatus.NEEDS_REVISION})
_IN_PROGRESS = frozenset({CaseStatus.DRAFT, CaseStatus.IN_REVIEW, CaseStatus.NEEDS_REVISION})


def create_case(
    db: Session, suite_id: uuid.UUID, payload: CaseWrite, *, actor_id: uuid.UUID
) -> CaseRead:
    """Store a new case as draft version 1. An inactive suite is rejected."""
    _require_active_suite(db, suite_id)
    katalog = legal_refs_service.load_catalog(db)
    _require_known_types(katalog, payload, stored=None)
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
    _write_version(db, case, version, payload, actor_id=actor_id)
    _catat_versi_baru(version)
    try:
        tersimpan = repository.create(db, case, version)
    except IntegrityError as galat:
        db.rollback()
        if not _code_clash(galat):
            raise
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan, version, katalog=katalog)


def preview_legal_refs(
    db: Session,
    legal_refs: list[dict[str, Any]],
    *,
    case_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> list[LegalRefPreviewItem]:
    """Pratinjau rujukan hukum dengan aturan simpan (PBI-21, SCRUM-162). Tidak menulis apa pun.

    case_id hanya dipakai untuk membaca kode jenis yang tersimpan di versi
    terbuka, seperti update_case, supaya pratinjau dan simpan sama-sama
    menerima rujukan lama yang jenisnya sudah dinonaktifkan. Kasusnya harus
    milik pengguna ini atau dibuka admin, sama seperti mengubah kasus.
    """
    katalog = legal_refs_service.load_catalog(db)
    tersimpan: set[str] = set()
    if case_id is not None:
        case = _require_case(db, case_id)
        _require_creator_or_admin(case, actor_id=actor_id, is_admin=is_admin)
        tersimpan = _stored_type_codes(
            katalog, (case.current_version.content or {}).get("legal_refs")
        )
    return legal_ref_preview.preview_citations(
        legal_refs,
        aktif=katalog.active,
        nama=katalog.names,
        tersimpan=tersimpan,
        tahun_ini=validation.current_year_wib(),
    )


def get_case(db: Session, case_id: uuid.UUID) -> CaseRead:
    """Return the version that is in effect, or raise when the id does not exist."""
    return _to_read(_require_case(db, case_id), katalog=legal_refs_service.load_catalog(db))


def get_completeness(db: Session, case_id: uuid.UUID) -> CaseCompleteness:
    """Kelengkapan versi yang sedang dikerjakan, dihitung ulang dari isinya.

    Dihitung dari content versi saat ini, bukan dari salinan completeness
    yang tersimpan, supaya baris lama tetap menjawab dengan formula terbaru.
    """
    case = _require_case(db, case_id)
    katalog = legal_refs_service.load_catalog(db)
    return CaseCompleteness(
        **completeness.from_row(case, _citations(case.current_version, katalog))
    )


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
    katalog = legal_refs_service.load_catalog(db)
    return [_to_summary(case, version, katalog) for case, version in baris]


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
    katalog = legal_refs_service.load_catalog(db)
    _require_known_types(katalog, payload, stored=(version.content or {}).get("legal_refs"))
    if payload.case_code != case.case_code:
        _require_free_code(db, payload.case_code)
    _write_version(db, case, version, payload, actor_id=actor_id)
    try:
        tersimpan = repository.save(db, case)
    except IntegrityError as galat:
        db.rollback()
        if not _code_clash(galat):
            raise
        raise _code_taken(db, payload.case_code) from None
    return _to_read(tersimpan, version, katalog=katalog)


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
    katalog = legal_refs_service.load_catalog(db)
    # The new draft gets its own rows, built from the approved content by the
    # same writer as a save, so Sprint 1 content lands in the PBI-21 shape.
    legal_refs_service.replace_for_version(
        db, version_id, katalog.citations((approved.content or {}).get("legal_refs"))
    )
    _catat_versi_baru(version)
    try:
        repository.create(db, case, version)
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "Sudah ada versi yang masih berupa draf atau sedang ditinjau",
            code=VERSION_IN_PROGRESS,
        ) from None
    return _to_read(case, version, katalog=katalog)


def submit_for_review(db: Session, case_id: uuid.UUID, *, actor_id: uuid.UUID) -> ReviewSubmission:
    """Ajukan versi yang sedang dikerjakan untuk direview (PBI-6 AC1, D3 langkah 1-12).

    Hanya pembuat kasus, hanya versi draft atau needs_revision, dan hanya
    bila guard D2 lolos. Draf membuka round lalu menugaskan dua reviewer.
    Versi needs_revision membuka round berikutnya dan membawa approval yang
    masih berlaku. Status versi dan round baru disimpan dalam satu commit.
    """
    case = _require_case(db, case_id)
    _require_active_suite(db, case.suite_id)
    if case.created_by != actor_id:
        raise ForbiddenError("Hanya penulis kasus yang boleh mengajukannya untuk direview")
    version = case.current_version
    _require_submittable(version)
    status_awal = version.status
    katalog = legal_refs_service.load_catalog(db)
    kurang = completeness.review_blockers(
        completeness.row_data(case, version, _citations(version, katalog))
    )
    if kurang:
        raise ValidationError(
            "Kasus belum siap diajukan. Lengkapi bagian yang disebut di `missing`.",
            code=CASE_NOT_READY,
            details={"missing": kurang},
        )
    version.status = CaseStatus.IN_REVIEW
    case.updated_by = actor_id
    tertunda = DeferredNotifier()
    try:
        if status_awal == CaseStatus.NEEDS_REVISION:
            ronde = reviews_service.resume_after_revision(
                db, case_id=case.id, case_version_id=version.id, notifier=tertunda
            )
        else:
            # open_round assigns on round 1 (SCRUM-144); do not call assign_on_submit here.
            ronde = reviews_service.open_round(
                db,
                case_id=case.id,
                case_version_id=version.id,
                author_id=actor_id,
                notifier=tertunda,
            )
        repository.save(db, case)
    except IntegrityError:
        # Pengajuan lain untuk versi yang sama menang lebih dulu.
        db.rollback()
        raise _not_submittable("Versi ini sudah diajukan dan sedang ditinjau.") from None
    tertunda.send(DbNotifier())
    return ReviewSubmission(
        case_id=case.id,
        version=version.version_no,
        status=version.status,
        round_no=ronde.round_no,
        round_status=str(ronde.status),
    )


def get_open_version(
    db: Session,
    case_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> CaseRead:
    """Return the version still being worked on, not the one in effect.

    GET /cases/{id} always shows the approved wording, so this is how the
    creator or an admin reopens a draft after leaving the page. Only the
    creator or an admin may read it, same as starting the version. A case
    with no draft, review or revision open is a 404 NO_OPEN_VERSION.
    """
    case = _require_case(db, case_id)
    _require_creator_or_admin(case, actor_id=actor_id, is_admin=is_admin)
    if case.current_version.status not in _IN_PROGRESS:
        raise NotFoundError(
            "Kasus ini tidak punya versi yang sedang dikerjakan", code=NO_OPEN_VERSION
        )
    return _to_read(case, case.current_version, katalog=legal_refs_service.load_catalog(db))


def change_split_tag(
    db: Session,
    case_id: uuid.UUID,
    split_tag: SplitTag,
    *,
    actor_id: uuid.UUID,
    confirm: bool,
) -> CaseRead:
    """Ubah tag dev/test kasus yang sudah disetujui (PBI-9 AC3, D4a aturan 1).

    Hanya Admin; router yang menjaga perannya. Versi yang berlaku dan versi
    yang masih terbuka ikut diubah, supaya menyetujui versi berikutnya tidak
    membawa kembali tag lama. Dev ke test harus dikonfirmasi karena kasusnya
    sudah pernah terlihat developer AI, dan kasus ditandai was_dev
    selamanya. Perubahan tercatat sebagai case.tag_changed lewat audit.
    """
    case = _require_case(db, case_id)
    _require_active_suite(db, case.suite_id)
    approved = case.latest_approved_version
    if approved is None:
        raise ConflictError(
            "Kasus ini belum disetujui. Sebelum disetujui, tag diubah penulisnya "
            "lewat PUT /cases/{id}.",
            code=NO_APPROVED_VERSION,
        )
    if approved.split_tag == split_tag:
        return _to_read(case, katalog=legal_refs_service.load_catalog(db))
    ke_test = approved.split_tag == SplitTag.DEV and split_tag == SplitTag.TEST
    if ke_test and not confirm:
        raise ConflictError(
            "Kasus ini sudah pernah terlihat developer AI sebagai dev case. "
            "Kirim ulang dengan confirm = true untuk tetap memindahkannya ke test.",
            code=TAG_CHANGE_NEEDS_CONFIRMATION,
            details={"requires_confirmation": True},
        )
    approved.split_tag = split_tag
    terbuka = case.current_version
    if terbuka.id != approved.id and terbuka.status in _IN_PROGRESS:
        terbuka.split_tag = split_tag
    if ke_test:
        case.was_dev = True
    case.updated_by = actor_id
    repository.save(db, case)
    return _to_read(case, katalog=legal_refs_service.load_catalog(db))


def list_case_versions(db: Session, case_id: uuid.UUID) -> list[VersionSummary]:
    """Every version, with the sections that differ from the previous one.

    Version 1 has an empty changed list. The diff is computed here, not stored.
    """
    case = _require_case(db, case_id)
    versions = repository.list_versions(db, case.id)
    names = auth_service.user_names(db, {version.created_by for version in versions})
    katalog = legal_refs_service.load_catalog(db)
    previous: dict | None = None
    rows: list[VersionSummary] = []
    for version in versions:
        current = _section_values(version, katalog)
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
    katalog = legal_refs_service.load_catalog(db)
    left_sections = _section_values(left, katalog)
    right_sections = _section_values(right, katalog)
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
    katalog = legal_refs_service.load_catalog(db)
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
                    legal_refs=_legal_refs_json(version, katalog),
                ),
            }
        )
    return copies


def review_copies(db: Session, version_ids: set[uuid.UUID]) -> dict[uuid.UUID, ReviewCopy]:
    """Isi versi untuk halaman reviewer, per case_version_id (PBI-6, SCRUM-145).

    Dipanggil reviews.service, yang tidak boleh membaca tabel cases sendiri.
    """
    if not version_ids:
        return {}
    katalog = legal_refs_service.load_catalog(db)
    return {
        version.id: _review_copy(case, version, katalog)
        for version, case in repository.versions_with_case(db, version_ids)
    }


def official_cases(db: Session, suite_id: uuid.UUID) -> list[ReviewCopy]:
    """Kasus yang masuk skor resmi: versi berlaku yang disetujui dan bertag test.

    PBI-9 AC4. Fondasi untuk penilaian dan laporan (Sprint 3): dev case
    tidak pernah masuk official accuracy, dan kasus yang belum disetujui
    belum menjadi bagian benchmark. Urut case_code supaya hasilnya stabil.

    Kasus yang pernah dev lalu dipindah ke test (cases.was_dev) tetap ikut,
    tanpa penanda tambahan di laporan. Ini keputusan klien (Kak Mario, lewat
    PO di review #53): setiap eksekusi berjalan pada snapshot kasus, jadi
    perubahan tag atau isi kasus yang hidup tidak pernah mengubah hasil
    yang sudah ada (PBI-11, SCRUM-76).
    """
    katalog = legal_refs_service.load_catalog(db)
    return [
        _review_copy(case, version, katalog)
        for case, version in repository.approved_for_suite(db, suite_id, split_tag=SplitTag.TEST)
    ]


def _review_copy(
    case: Case, version: CaseVersion, katalog: legal_refs_service.TypeCatalog
) -> ReviewCopy:
    """Isi satu versi beserta kode, penulis, tag, dan rujukan dalam bentuk API (SCRUM-160)."""
    isi = version.content or {}
    return ReviewCopy(
        case_id=case.id,
        case_code=version.case_code,
        author_id=case.created_by,
        case_version_id=version.id,
        version_no=version.version_no,
        split_tag=version.split_tag,
        identity={
            "title": isi.get("title") or "",
            "question": isi.get("question") or "",
            "category": isi.get("category"),
        },
        legal_refs=_legal_refs_read(version, katalog),
        answer_criteria=isi.get("answer_criteria") or {},
        traps=isi.get("traps") or [],
    )


def case_author(db: Session, case_id: uuid.UUID) -> uuid.UUID:
    """Penulis kasus, yaitu pembuatnya. Dipakai aturan "reviewer bukan penulis" (AC2)."""
    return _require_case(db, case_id).created_by


def apply_review_decision(db: Session, case_version_id: uuid.UUID, *, approved: bool) -> None:
    """Terapkan keputusan round ke versi yang sedang direview (AC4-AC6, D3 langkah 31/36).

    Disetujui: versi jadi approved dan menjadi versi yang berlaku. Revisi:
    versi kembali ke penulis sebagai needs_revision. Tidak commit, karena
    verdict dan keputusannya harus tersimpan bersama di transaksi reviews.
    """
    version = repository.get_version_by_id(db, case_version_id)
    if version is None:
        raise NotFoundError("Versi tidak ditemukan")
    if version.status != CaseStatus.IN_REVIEW:
        raise ConflictError("Versi ini tidak sedang ditinjau", code=VERSION_NOT_IN_REVIEW)
    if not approved:
        version.status = CaseStatus.NEEDS_REVISION
        return
    version.status = CaseStatus.APPROVED
    case = repository.get_by_id(db, version.case_id)
    case.latest_approved_version_id = version.id


def count_by_status(db: Session) -> dict[CaseStatus, int]:
    """Jumlah kasus per status versi yang berlaku. Dipanggil modul dashboard (SCRUM-154).

    Setiap status selalu ada di hasil, nol bila tidak ada kasusnya.
    """
    counts = repository.count_by_status(db)
    return {status: counts.get(status, 0) for status in CaseStatus}


def case_codes(db: Session, case_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """case_code per id for the audit log object column (F-06).

    Ids with no case row are left out.
    """
    if not case_ids:
        return {}
    return repository.codes_by_ids(db, case_ids)


def find_case_ids(db: Session, text: str) -> list[uuid.UUID]:
    """Ids of cases whose code contains text, for the audit "Kode kasus" search (F-06)."""
    return repository.ids_with_code_containing(db, text)


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


def _section_values(version: CaseVersion, katalog: legal_refs_service.TypeCatalog) -> dict:
    """The eight sections for one version, including its stored case_code.

    Citations are read like every other endpoint, so a Sprint 1 citation saved
    again in the PBI-21 shape compares equal to its old self.
    """
    return sections.sections_from(
        case_code=version.case_code,
        split_tag=version.split_tag.value,
        content=version.content,
        legal_refs=_legal_refs_json(version, katalog),
    )


def _legal_refs_json(version: CaseVersion, katalog: legal_refs_service.TypeCatalog) -> list[dict]:
    """The read citations as plain JSON, for sections that are compared or frozen."""
    return [rujukan.model_dump(mode="json") for rujukan in _legal_refs_read(version, katalog)]


def _catat_versi_baru(version: CaseVersion) -> None:
    """TODO(SCRUM-140): record the new-version event in audit_logs.

    The audit table and listener belong to that ticket. This call site is
    the only place a new case_versions row is inserted.
    """
    del version


def _write_version(
    db: Session, case: Case, version: CaseVersion, payload: CaseWrite, *, actor_id: uuid.UUID
) -> None:
    """Copy the write body onto the version, with its completeness and its citation rows.

    The one place a save writes: content["legal_refs"] (what the API reads)
    and the SCRUM-159 tables go into the same session, so the caller's
    commit stores both or neither (PBI-21 dual write).
    """
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
    legal_refs_service.replace_for_version(db, version.id, data["legal_refs"])


def _to_read(
    case: Case,
    version: CaseVersion | None = None,
    *,
    katalog: legal_refs_service.TypeCatalog,
) -> CaseRead:
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
        legal_refs=_legal_refs_read(versi, katalog),
        answer_criteria=isi.get("answer_criteria") or {},
        traps=isi.get("traps") or [],
        split_tag=versi.split_tag,
        status=versi.status,
        # None pada objek Case yang belum di-flush; default kolomnya false.
        was_dev=bool(case.was_dev),
        completeness_pct=_completeness_pct(isi.get("completeness")),
        version=versi.version_no,
        created_at=versi.created_at,
        updated_at=versi.updated_at,
    )


def _to_summary(
    case: Case, version: CaseVersion, katalog: legal_refs_service.TypeCatalog
) -> CaseSummary:
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
        completeness_pct=_pct_from_version(case, version, katalog),
        updated_at=version.updated_at,
    )


def _in_effect(case: Case) -> CaseVersion:
    """The approved wording when one exists, otherwise the only open version."""
    if case.latest_approved_version is not None:
        return case.latest_approved_version
    return case.current_version


def _pct_from_version(
    case: Case, version: CaseVersion, katalog: legal_refs_service.TypeCatalog
) -> int:
    """Recalculate completeness from the version being shown."""
    data = completeness.row_data(case, version, _citations(version, katalog))
    return int(completeness.evaluate(data)["pct"])


def _citations(version: CaseVersion, katalog: legal_refs_service.TypeCatalog) -> list[dict]:
    """The version's citations in the PBI-21 shape, Sprint 1 content included (AC9)."""
    return katalog.citations((version.content or {}).get("legal_refs"))


def _legal_refs_read(
    version: CaseVersion, katalog: legal_refs_service.TypeCatalog
) -> list[LegalRefRead]:
    """Citations as returned by the API, each with its display_text (AC6, AC10)."""
    hasil: list[LegalRefRead] = []
    for isi in _citations(version, katalog):
        tersimpan = LegalRefStored.model_validate(isi)
        hasil.append(
            LegalRefRead(
                **tersimpan.model_dump(), display_text=citation_text(tersimpan, katalog.names)
            )
        )
    return hasil


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


def _require_submittable(version: CaseVersion) -> None:
    """Hanya draft atau needs_revision yang bisa diajukan."""
    if version.status in _EDITABLE:
        return
    if version.status == CaseStatus.IN_REVIEW:
        raise _not_submittable("Versi ini sudah diajukan dan sedang ditinjau.")
    raise _not_submittable("Versi ini sudah disetujui. Buat versi baru untuk mengajukan perubahan.")


def _not_submittable(message: str) -> ConflictError:
    return ConflictError(message, code=VERSION_NOT_SUBMITTABLE)


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


def _require_known_types(
    katalog: legal_refs_service.TypeCatalog, payload: CaseWrite, *, stored: object
) -> None:
    """AC2: link types are in the list; a deactivated one only if this version stored it."""
    data = payload.model_dump(mode="json")
    validation.check_regulation_types(
        data["legal_refs"], katalog.active, _stored_type_codes(katalog, stored)
    )


def _stored_type_codes(katalog: legal_refs_service.TypeCatalog, stored: object) -> set[str]:
    """Kode jenis di rujukan yang sudah tersimpan, termasuk rujukan Sprint 1 (AC9)."""
    return {
        link["regulation_type"]
        for rujukan in katalog.citations(stored)
        for link in rujukan["links"]
    }


def _require_free_code(db: Session, case_code: str) -> None:
    """Reject a case_code that another suite already owns."""
    if repository.get_by_code(db, case_code) is not None:
        raise _code_taken(db, case_code)


# Only this constraint means two cases want the same code: its name on
# PostgreSQL, its column on SQLite. Since PBI-21 the same commit also writes the
# legal_refs rows, and their errors must not be reported as a code clash.
_BENTROK_KODE = ("uq_cases_case_code", "cases.case_code")


def _code_clash(galat: IntegrityError) -> bool:
    pesan = str(galat.orig)
    return any(tanda in pesan for tanda in _BENTROK_KODE)


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
    """Allow the creator or an admin. split_tag belongs to the creator before
    approval and to the Admin PATCH after it (PBI-9 AC3).

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
    if payload.split_tag == version.split_tag:
        return
    if not belum_disetujui:
        # PBI-9 AC3: setelah disetujui, tag hanya diubah Admin lewat PATCH,
        # yang meminta konfirmasi dev ke test dan mencatat penanda was_dev.
        # 409 seperti CASE_CODE_LOCKED: biasanya ini state basi (Admin baru saja
        # mengubah tag), bukan soal izin, jadi client tetap di halaman.
        raise ConflictError(
            "Tag kasus ini dikunci setelah disetujui dan mungkin baru saja diubah Admin. "
            "Muat ulang halaman lalu simpan lagi.",
            code=SPLIT_TAG_LOCKED,
        )
    if case.created_by != actor_id:
        raise ForbiddenError(
            "Sebelum kasus disetujui, hanya pembuat yang boleh mengubah split_tag",
            code=SPLIT_TAG_LOCKED,
        )
