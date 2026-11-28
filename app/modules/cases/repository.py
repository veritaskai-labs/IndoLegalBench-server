"""Akses database modul cases.

ATURAN: hanya cases/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid
from collections.abc import Iterable, Iterator, Mapping

from sqlalchemy import func, literal, or_, select, union
from sqlalchemy.orm import Session, aliased, selectinload

from app.modules.cases.case_sorting import DEFAULT_SORT, CaseSortKey, SortOrder, build_order_by
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag


def get_by_id(db: Session, case_id: uuid.UUID) -> Case | None:
    """Return the case with both version pointers loaded, or None."""
    return (
        db.query(Case)
        .options(
            selectinload(Case.current_version),
            selectinload(Case.latest_approved_version),
        )
        .filter(Case.id == case_id)
        .one_or_none()
    )


def get_by_code(db: Session, case_code: str) -> Case | None:
    """Find a case_code across every suite. The code is globally unique."""
    return db.query(Case).filter(Case.case_code == case_code).one_or_none()


def _in_effect_version_id():
    """The version a case shows: the latest approved one, else the current one.

    One definition for the per-suite list, the case directory and the dashboard
    counts, so they can never disagree about a case's status or title.
    """
    return func.coalesce(Case.latest_approved_version_id, Case.current_version_id)


def search_candidates(
    db: Session, *, approved_dev_only: bool
) -> Iterator[tuple[Case, CaseVersion]]:
    version_id = func.coalesce(Case.latest_approved_version_id, Case.current_version_id)
    if approved_dev_only:
        version_id = Case.latest_approved_version_id
    statement = (
        select(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == version_id)
        .order_by(func.lower(Case.case_code), Case.id)
    )
    if approved_dev_only:
        statement = statement.where(
            CaseVersion.status == CaseStatus.APPROVED, CaseVersion.split_tag == SplitTag.DEV
        )
    yield from db.execute(statement.execution_options(yield_per=100)).tuples()


def list_for_suite(
    db: Session,
    suite_id: uuid.UUID,
    *,
    status: CaseStatus | None = None,
    split_tag: SplitTag | None = None,
) -> list[tuple[Case, CaseVersion]]:
    """List a suite's cases by the version that is in effect.

    That version is the latest approved one when it exists, otherwise the
    current version. Newest update of that version comes first.
    """
    in_effect_id = _in_effect_version_id()
    query = (
        db.query(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == in_effect_id)
        .filter(Case.suite_id == suite_id)
    )
    if status is not None:
        query = query.filter(CaseVersion.status == status)
    if split_tag is not None:
        query = query.filter(CaseVersion.split_tag == split_tag)
    return query.order_by(*build_order_by()).all()


def page_in_effect(
    db: Session,
    *,
    suite_ids: Iterable[uuid.UUID],
    keyword: str | None = None,
    status: CaseStatus | None = None,
    suite_id: uuid.UUID | None = None,
    sort: CaseSortKey = DEFAULT_SORT,
    order: SortOrder | None = None,
    suite_rank: Mapping[uuid.UUID, int] | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[tuple[Case, CaseVersion]], int]:
    """One page of cases with the version in effect, across suites, and the total (SCRUM-339).

    Same in-effect rule and order as list_for_suite, so the directory and the
    per-suite list never disagree. status filters on the in-effect version, so
    an approved case with an open revision counts as approved (like
    count_by_status). keyword matches part of the case code or of the in-effect
    title, ignoring letter case, with % _ and \\ taken literally. Filters
    combine with AND. Only cases of suite_ids are listed, so cases of a deleted
    suite never show (SCRUM-339 fix). Paging happens in SQL. The order comes from
    case_sorting.build_order_by (SCRUM-379): the chosen column, then the case code, so pages
    never overlap when values tie. Sorting never adds a join or filter, so the rows and the
    total are the same whatever the order.
    """
    in_effect_id = _in_effect_version_id()
    query = (
        db.query(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == in_effect_id)
        .filter(Case.suite_id.in_(list(suite_ids)))
    )
    if status is not None:
        query = query.filter(CaseVersion.status == status)
    if suite_id is not None:
        query = query.filter(Case.suite_id == suite_id)
    if keyword is not None:
        query = query.filter(*_keyword_filters(keyword))
    total = query.count()
    rows = (
        query.order_by(*build_order_by(sort, order, suite_rank)).offset(offset).limit(limit).all()
    )
    return rows, total


def _keyword_filters(keyword: str) -> tuple:
    """Filters for part of the case code or of the in-effect title (SCRUM-339).

    Two steps, so PostgreSQL can use the trigram indexes (migration ff8f38c22a8e):
    1. candidate case ids from each index on its own: codes that match, plus cases
       with any version whose title matches;
    2. the exact rule on the joined in-effect row, so a draft revision's title
       never makes an approved case match.
    A single OR across the two joined tables cannot use either index (measured:
    two sequential scans over every row). The subqueries use aliases so they are
    not correlated with the outer query.

    The pattern is lowercased by the database's own lower(), the same function
    as the columns and the indexes. Python's str.lower() can disagree with it
    ("İ" becomes "i" plus a combining dot), so a keyword copied from a title
    would miss. Lowercasing never touches the % _ and \\ escapes.
    """
    pola = func.lower(literal("%" + _escape_like(keyword) + "%"))
    kasus, versi = aliased(Case), aliased(CaseVersion)
    calon = union(
        select(kasus.id).where(func.lower(kasus.case_code).like(pola, escape="\\")),
        select(versi.case_id).where(
            func.lower(versi.content["title"].as_string()).like(pola, escape="\\")
        ),
    )
    judul = CaseVersion.content["title"].as_string()
    return (
        Case.id.in_(calon),
        or_(
            func.lower(Case.case_code).like(pola, escape="\\"),
            func.lower(judul).like(pola, escape="\\"),
        ),
    )


def count_by_status(db: Session, *, created_by: uuid.UUID | None = None) -> dict[CaseStatus, int]:
    """Count cases by the status of their in-effect version, in one GROUP BY.

    Same in-effect rule as list_for_suite, so the numbers match the case list.
    created_by limits the count to one writer's cases (PBI-24 AC6); None counts
    every case.
    """
    in_effect_id = _in_effect_version_id()
    query = db.query(CaseVersion.status, func.count(Case.id)).join(
        CaseVersion, CaseVersion.id == in_effect_id
    )
    if created_by is not None:
        query = query.filter(Case.created_by == created_by)
    return dict(query.group_by(CaseVersion.status).all())


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count every case in the suite, whatever its status."""
    return db.query(Case).filter(Case.suite_id == suite_id).count()


def count_approved_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count the suite's cases that have an approved version, dev and test."""
    return (
        db.query(Case)
        .filter(Case.suite_id == suite_id, Case.latest_approved_version_id.is_not(None))
        .count()
    )


def count_approved_by_split(db: Session, suite_ids: set[uuid.UUID]) -> dict[SplitTag, int]:
    """Count cases with an approved version per split tag, across the suites.

    The tag is read from latest_approved_version, not from an open draft.
    A split tag with no case is absent from the result.
    """
    rows = (
        db.query(CaseVersion.split_tag, func.count(Case.id))
        .join(Case, CaseVersion.id == Case.latest_approved_version_id)
        .filter(Case.suite_id.in_(suite_ids))
        .group_by(CaseVersion.split_tag)
        .all()
    )
    return dict(rows)


def approved_questions_by_split(
    db: Session, suite_ids: set[uuid.UUID]
) -> list[tuple[SplitTag, str]]:
    """Question text of the latest approved version, ordered by case_code.

    The text is content["question"]. The split tag is read from that approved
    version, not from an open draft. A suite with no approved case adds no row.
    """
    rows = (
        db.query(CaseVersion.split_tag, CaseVersion.content)
        .join(Case, CaseVersion.id == Case.latest_approved_version_id)
        .filter(Case.suite_id.in_(suite_ids))
        .order_by(Case.case_code.asc())
        .all()
    )
    return [(SplitTag(split), (content or {}).get("question") or "") for split, content in rows]


def has_approved(db: Session, suite_id: uuid.UUID) -> bool:
    """True when any case in the suite has an approved version."""
    return (
        db.query(Case.id)
        .filter(Case.suite_id == suite_id, Case.latest_approved_version_id.is_not(None))
        .first()
        is not None
    )


def list_versions(db: Session, case_id: uuid.UUID) -> list[CaseVersion]:
    """Every version of one case, oldest version_no first."""
    return (
        db.query(CaseVersion)
        .filter(CaseVersion.case_id == case_id)
        .order_by(CaseVersion.version_no.asc())
        .all()
    )


def get_version(db: Session, case_id: uuid.UUID, version_no: int) -> CaseVersion | None:
    """One version of this case, or None when that number is not stored."""
    return (
        db.query(CaseVersion)
        .filter(CaseVersion.case_id == case_id, CaseVersion.version_no == version_no)
        .one_or_none()
    )


def approved_for_suite(
    db: Session, suite_id: uuid.UUID, *, split_tag: SplitTag | None = None
) -> list[tuple[Case, CaseVersion]]:
    """Cases in the suite that have an approved version, with that version.

    The version is latest_approved_version, not an open draft. Ordered by
    case_code so a snapshot's items are stable. split_tag narrows the result
    to the dev or the test set.
    """
    query = (
        db.query(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == Case.latest_approved_version_id)
        .filter(Case.suite_id == suite_id, Case.latest_approved_version_id.is_not(None))
    )
    if split_tag is not None:
        query = query.filter(CaseVersion.split_tag == split_tag)
    return query.order_by(Case.case_code.asc()).all()


def get_version_by_id(db: Session, version_id: uuid.UUID) -> CaseVersion | None:
    """One version by its id, or None."""
    return db.get(CaseVersion, version_id)


def versions_with_case(db: Session, version_ids: set[uuid.UUID]) -> list[tuple[CaseVersion, Case]]:
    """Each requested version together with its case row."""
    return (
        db.query(CaseVersion, Case)
        .join(Case, Case.id == CaseVersion.case_id)
        .filter(CaseVersion.id.in_(version_ids))
        .all()
    )


def codes_by_ids(db: Session, case_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """case_code per case id. Ids with no case row are left out."""
    rows = db.query(Case.id, Case.case_code).filter(Case.id.in_(case_ids)).all()
    return dict(rows)


def ids_with_code_containing(db: Session, text: str) -> list[uuid.UUID]:
    """Ids of cases whose code contains text, ignoring case.

    '%' and '_' from the caller are matched literally.
    """
    pola = "%" + _escape_like(text.lower()) + "%"
    rows = (
        db.query(Case.id)
        .filter(func.lower(Case.case_code).like(pola, escape="\\"))
        .order_by(Case.case_code)
        .all()
    )
    return [case_id for (case_id,) in rows]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def max_version_no(db: Session, case_id: uuid.UUID) -> int:
    """Highest version_no stored for the case, or 0 when it has none."""
    tertinggi = (
        db.query(func.max(CaseVersion.version_no)).filter(CaseVersion.case_id == case_id).scalar()
    )
    return int(tertinggi or 0)


def create(db: Session, case: Case, version: CaseVersion) -> Case:
    """Insert the case and its first version in one commit."""
    db.add(case)
    db.add(version)
    db.commit()
    db.refresh(case)
    db.refresh(version)
    return case


def save(db: Session, case: Case) -> Case:
    """Commit changes on an existing case and its versions, then refresh it."""
    db.commit()
    db.refresh(case)
    return case
