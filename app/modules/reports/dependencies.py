"""Perakitan dependency FastAPI untuk modul reports (PBI-15, SCRUM-313).

Satu-satunya tempat yang tahu pembaca sesi mana yang dipakai pembuat laporan. Test mengganti
get_session_record_reader dengan pembaca palsu lewat dependency_overrides.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.session_reader import SqlSessionRecordReader
from app.modules.reports.session_record import SessionRecordReader
from app.shared.database import get_db


def get_session_record_reader(db: Session = Depends(get_db)) -> SessionRecordReader:
    return SqlSessionRecordReader(db)


def get_report_generator(
    db: Session = Depends(get_db),
    reader: SessionRecordReader = Depends(get_session_record_reader),
) -> ReportGenerator:
    return ReportGenerator(db, reader)


def get_eligible_session_lister(
    reader: SessionRecordReader = Depends(get_session_record_reader),
) -> EligibleSessionLister:
    return EligibleSessionLister(reader)
