"""Perakitan dependency FastAPI untuk modul reports (PBI-15, SCRUM-313; PBI-17, SCRUM-331).

Satu-satunya tempat yang tahu pembaca sesi mana yang dipakai pembuat laporan. Test mengganti
get_session_record_reader dengan pembaca palsu lewat dependency_overrides.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.score_file_repository import SqlScoreFileStore, SqlScoreSource
from app.modules.reports.score_files import (
    ScoreFileStore,
    ScoreSource,
    SessionScoreFileService,
)
from app.modules.reports.session_reader import SqlSessionRecordReader
from app.modules.reports.session_record import SessionRecordReader
from app.modules.reports.trend_catalog import SqlProductCatalog
from app.modules.reports.trend_service import ProductCatalog, TrendService
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


def get_product_catalog(db: Session = Depends(get_db)) -> ProductCatalog:
    return SqlProductCatalog(db)


def get_trend_service(
    reader: SessionRecordReader = Depends(get_session_record_reader),
    catalog: ProductCatalog = Depends(get_product_catalog),
) -> TrendService:
    return TrendService(reader, catalog)


def get_score_source(db: Session = Depends(get_db)) -> ScoreSource:
    """PBI-16 SCRUM-327. Test mengganti sumber ini; penyimpanannya tetap adapter SQL."""
    return SqlScoreSource(db)


def get_score_file_store(db: Session = Depends(get_db)) -> ScoreFileStore:
    return SqlScoreFileStore(db)


def get_score_file_service(
    source: ScoreSource = Depends(get_score_source),
    store: ScoreFileStore = Depends(get_score_file_store),
) -> SessionScoreFileService:
    return SessionScoreFileService(source, store)
