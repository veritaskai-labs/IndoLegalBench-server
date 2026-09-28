"""Akses database modul cases.

ATURAN: hanya cases/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.cases.models import Case, CaseStatus, SplitTag


def get_by_id(db: Session, case_id: uuid.UUID) -> Case | None:
    """Return the row, or None when the id is unknown."""
    return db.get(Case, case_id)


def get_by_code(db: Session, case_code: str) -> Case | None:
    """Find a case_code across every suite. The code is globally unique."""
    return db.query(Case).filter(Case.case_code == case_code).one_or_none()


def list_for_suite(
    db: Session,
    suite_id: uuid.UUID,
    *,
    status: CaseStatus | None = None,
    split_tag: SplitTag | None = None,
) -> list[Case]:
    """List a suite's cases, newest update first, then by case_code."""
    query = db.query(Case).filter(Case.suite_id == suite_id)
    if status is not None:
        query = query.filter(Case.status == status)
    if split_tag is not None:
        query = query.filter(Case.split_tag == split_tag)
    return query.order_by(Case.updated_at.desc(), Case.case_code.asc()).all()


def count_for_suite(db: Session, suite_id: uuid.UUID) -> int:
    """Count every case in the suite, whatever its status."""
    return db.query(Case).filter(Case.suite_id == suite_id).count()


def has_approved(db: Session, suite_id: uuid.UUID) -> bool:
    """True when any case in the suite is approved. Used by suite delete."""
    return (
        db.query(Case.id)
        .filter(Case.suite_id == suite_id, Case.status == CaseStatus.APPROVED)
        .first()
        is not None
    )


def create(db: Session, case: Case) -> Case:
    """Insert the row and return it with database defaults filled in."""
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def save(db: Session, case: Case) -> Case:
    """Commit changes on an existing row and refresh it."""
    db.commit()
    db.refresh(case)
    return case
