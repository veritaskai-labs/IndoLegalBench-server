"""Data sintetis untuk tes tabel laporan SCRUM-300 di SQLite (L3, tanpa data klien).

SQLite di tests/conftest.py tidak menyalakan PRAGMA foreign_keys, jadi kasus dan versi
kasus cukup UUID acak: yang diuji di sini aturan kolom milik tabel laporan, bukan isi
kasus. FK, trigger, dan "kasus milik versinya" diuji di PostgreSQL
(tests/integration/test_report_tables_pg.py).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.cases.models import SplitTag
from app.modules.grading.models import RubricVersion
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    PublishedReport,
    Report,
    ReportCaseBasis,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)
from app.modules.runs.models import MeasurementSession, SessionPurpose, SessionType, SplitFilter
from app.shared.security import Role


def buat_admin(db: Session) -> User:
    admin = User(
        email=f"admin-300-{uuid.uuid4().hex}@veritask.test", name="Admin 300", role=Role.ADMIN
    )
    db.add(admin)
    db.flush()
    return admin


def buat_rubrik(db: Session, admin: User) -> RubricVersion:
    rubrik = RubricVersion(
        version_no=1,
        effective_date=date(2026, 11, 1),
        judge_prompt_quality="Prompt kualitas sintetis.",
        judge_prompt_format="Prompt format sintetis.",
        created_by=admin.id,
    )
    db.add(rubrik)
    db.flush()
    return rubrik


def buat_sesi(db: Session, admin: User, nama_produk: str) -> MeasurementSession:
    produk = AiProduct(
        name=nama_produk,
        created_by=admin.id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Penyedia Uji",
        site_url="https://contoh.test",
    )
    db.add(produk)
    db.flush()
    sesi = MeasurementSession(
        label=f"Sesi {nama_produk}",
        purpose=SessionPurpose.OFFICIAL,
        session_type=SessionType.MANUAL,
        product_id=produk.id,
        repetitions=1,
        split_filter=SplitFilter.TEST,
        created_by=admin.id,
    )
    db.add(sesi)
    db.flush()
    return sesi


def laporan(admin: User, rubrik: RubricVersion, **ubah) -> Report:
    isi = {
        "title": "Laporan Oktober",
        "score_scope": ScoreScope.TEST,
        "has_dev_scores": False,
        "measurement_filter": MeasurementFilter.ALL,
        "product_label_mode": ProductLabelMode.REAL_NAME,
        "rubric_version_id": rubrik.id,
        "judge_model_name": "judge-model-sintetis",
        "case_basis_count": 2,
        "created_by": admin.id,
    } | ubah
    return Report(id=uuid.uuid4(), **isi)


def sesi_laporan(report: Report, sesi: MeasurementSession, **ubah) -> ReportSession:
    isi = {
        "report_id": report.id,
        "session_id": sesi.id,
        "product_id": sesi.product_id,
        "product_label": "Produk A",
        "measurement_kind": MeasurementKind.MANUAL,
        "is_trial": False,
        "markers": [],
    } | ubah
    return ReportSession(**isi)


def hasil(report: Report, sesi: MeasurementSession, **ubah) -> ReportResult:
    isi = {
        "report_id": report.id,
        "session_id": sesi.id,
        "score_split": ScoreSplit.TEST,
        "cases_graded_count": 3,
        "cases_basis_count": 2,
        "det_score": Decimal("0.75"),
        "llm_score": Decimal("0.50"),
        "total_score": Decimal("0.63"),
        "failure_rate": None,
        "rank": 1,
        "repetitions": 1,
        "product_model_version": "model-a-2026-10",
        "judge_model_version": "judge-2026-10",
    } | ubah
    return ReportResult(**isi)


def dasar(report: Report, **ubah) -> ReportCaseBasis:
    isi = {
        "report_id": report.id,
        "case_id": uuid.uuid4(),
        "case_version_id": uuid.uuid4(),
        "split_tag": SplitTag.TEST,
    } | ubah
    return ReportCaseBasis(**isi)


def terbit(report: Report, admin: User, **ubah) -> PublishedReport:
    isi = {
        "id": uuid.uuid4(),
        "report_id": report.id,
        "version_no": 1,
        "published_by": admin.id,
        "supersedes_id": None,
    } | ubah
    return PublishedReport(**isi)
