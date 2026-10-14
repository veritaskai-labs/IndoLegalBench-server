"""Akses database modul cases.

ATURAN: hanya cases/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

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
    in_effect_id = func.coalesce(Case.latest_approved_version_id, Case.current_version_id)
    query = (
        db.query(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == in_effect_id)
        .filter(Case.suite_id == suite_id)
    )
    if status is not None:
        query = query.filter(CaseVersion.status == status)
    if split_tag is not None:
        query = query.filter(CaseVersion.split_tag == split_tag)
    return query.order_by(CaseVersion.updated_at.desc(), Case.case_code.asc()).all()


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count every case in the suite, whatever its status."""
    return db.query(Case).filter(Case.suite_id == suite_id).count()


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


def approved_for_suite(db: Session, suite_id: uuid.UUID) -> list[tuple[Case, CaseVersion]]:
    """Cases in the suite that have an approved version, with that version.

    The version is latest_approved_version, not an open draft. Ordered by
    case_code so a snapshot's items are stable.
    """
    return (
        db.query(Case, CaseVersion)
        .join(CaseVersion, CaseVersion.id == Case.latest_approved_version_id)
        .filter(Case.suite_id == suite_id, Case.latest_approved_version_id.is_not(None))
        .order_by(Case.case_code.asc())
        .all()
    )


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
