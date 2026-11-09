"""Status percobaan manual yang sudah terisi di laporan dan detail sesi. SCRUM-176 SCRUM-273.

PBI-22 D8: percobaan manual tetap pending di database dan ditandai terisi lewat filled_at.
Laporan penilaian dan detail sesi harus menampilkannya sebagai succeeded, sama seperti
daftar sesi, tanpa mengubah percobaan otomatis, gagal teknis, atau yang belum terisi.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.modules.cases.models import SplitTag
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    SessionStatus,
    SessionType,
)
from app.modules.runs.schemas import SessionGradingResponse
from app.modules.runs.session_detail import (
    AttemptRow,
    CaseRow,
    SessionDetailRow,
    SessionDetailService,
)
from app.modules.runs.session_grade import (
    AttemptGradeInput,
    CaseGradeInput,
    DbGradingStore,
    _load_case_grade_inputs,
    grade_session_data,
)
from app.modules.runs.session_list import SessionRow
from app.shared.security import Role
from tests.modules.runs.test_session_grade import _setup_sesi_lengkap, pembuat, produk

__all__ = ["pembuat", "produk"]

DIISI = datetime(2026, 11, 9, 4, 0, 0, tzinfo=UTC)

KASUS_STATUS = [
    pytest.param(
        AttemptSource.MANUAL,
        AttemptStatus.PENDING,
        DIISI,
        AttemptStatus.SUCCEEDED,
        id="manual-terisi-tampil-succeeded",
    ),
    pytest.param(
        AttemptSource.MANUAL,
        AttemptStatus.PENDING,
        None,
        AttemptStatus.PENDING,
        id="manual-belum-terisi-tetap-pending",
    ),
    pytest.param(
        AttemptSource.AUTOMATIC,
        AttemptStatus.PENDING,
        None,
        AttemptStatus.PENDING,
        id="otomatis-pending-tetap-pending",
    ),
    pytest.param(
        AttemptSource.AUTOMATIC,
        AttemptStatus.PENDING,
        DIISI,
        AttemptStatus.PENDING,
        id="otomatis-dengan-filled_at-tetap-pending",
    ),
    pytest.param(
        AttemptSource.MANUAL,
        AttemptStatus.TECHNICAL_FAILURE,
        DIISI,
        AttemptStatus.TECHNICAL_FAILURE,
        id="manual-gagal-teknis-tidak-berubah",
    ),
    pytest.param(
        AttemptSource.AUTOMATIC,
        AttemptStatus.RUNNING,
        None,
        AttemptStatus.RUNNING,
        id="otomatis-running-tidak-berubah",
    ),
]


@pytest.mark.parametrize(("source", "status", "filled_at", "expected"), KASUS_STATUS)
def test_laporan_penilaian_menampilkan_status_turunan(source, status, filled_at, expected):
    # Arrange
    kasus = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="MAN-001",
        title="Kasus Manual",
        split_tag=SplitTag.DEV,
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=status,
                source=source,
                filled_at=filled_at,
                answer_text="Jawaban dari tim legal",
                quality_score=Decimal("4.00"),
                format_score=Decimal("0.80"),
            )
        ],
    )

    # Act
    hasil = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=[kasus],
        triggered_by=uuid.uuid4(),
        rubric_version="v1",
    )

    # Assert
    assert isinstance(hasil, SessionGradingResponse)
    assert hasil.cases[0].attempts[0].status == expected


def test_laporan_penilaian_tidak_mengubah_skor_percobaan_manual_terisi():
    # Arrange
    kasus = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="MAN-002",
        title="Kasus Manual",
        split_tag=SplitTag.DEV,
        attempts=[
            AttemptGradeInput(
                attempt_id=uuid.uuid4(),
                repetition_no=1,
                status=AttemptStatus.PENDING,
                source=AttemptSource.MANUAL,
                filled_at=DIISI,
                answer_text="Jawaban dari tim legal",
                deterministic_score=Decimal("4.50"),
                quality_score=Decimal("4.50"),
                format_score=Decimal("0.80"),
            )
        ],
    )

    # Act
    hasil = grade_session_data(
        session_id=uuid.uuid4(),
        cases_input=[kasus],
        triggered_by=uuid.uuid4(),
        rubric_version="v1",
    )

    # Assert
    percobaan = hasil.cases[0].attempts[0]
    assert percobaan.is_technical_disruption is False
    assert percobaan.score is not None
    assert percobaan.score.quality == Decimal("4.50")


class _Sesi:
    def __init__(self, row: SessionDetailRow) -> None:
        self._row = row

    def detail(self, session_id: uuid.UUID) -> SessionDetailRow:
        return self._row


class _Nama:
    def product_names(self, product_ids):
        return {}

    def user_names(self, user_ids):
        return {}


def _detail_sesi(attempt: AttemptRow) -> SessionDetailRow:
    ringkasan = SessionRow(
        id=uuid.uuid4(),
        label="Sesi Manual",
        created_at=DIISI,
        created_by=uuid.uuid4(),
        product_id=uuid.uuid4(),
        repetitions=1,
        status=SessionStatus.GRADED,
        case_count=1,
        attempt_counts={"succeeded": 1},
        session_type=SessionType.MANUAL,
        completed_cases=1,
        total_cases=1,
    )
    kasus = CaseRow(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        body={"sections": {"split_tag": "test"}},
        attempts=[attempt],
    )
    return SessionDetailRow(
        summary=ringkasan,
        purpose="official",
        split_filter="both",
        snapshot_created_at=DIISI,
        started_at=DIISI,
        finished_at=DIISI,
        archived_at=None,
        stopped_at=None,
        stop_reason=None,
        accepted_as_is_at=None,
        accepted_as_is_by=None,
        accepted_as_is_reason=None,
        cases=[kasus],
    )


@pytest.mark.parametrize(("source", "status", "filled_at", "expected"), KASUS_STATUS)
def test_detail_sesi_menampilkan_status_turunan(source, status, filled_at, expected):
    # Arrange
    attempt = AttemptRow(
        id=uuid.uuid4(),
        repetition_no=1,
        status=status,
        answer_text=None,
        model_version=None,
        source=source,
        filled_at=filled_at,
    )
    service = SessionDetailService(_Sesi(_detail_sesi(attempt)), _Nama())

    # Act
    detail = service.get(uuid.uuid4(), viewer_role=Role.DEVELOPER_AI)

    # Assert
    assert detail.cases[0].attempts[0].status == expected


def test_laporan_tersimpan_menampilkan_manual_terisi_sebagai_succeeded(db_session, produk, pembuat):
    # Arrange: satu percobaan manual terisi yang di database tetap pending (PBI-22 D8)
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    target = db_session.scalars(select(Attempt).where(Attempt.session_id == sesi.id)).first()
    db_session.execute(
        update(Attempt)
        .where(Attempt.id == target.id)
        .values(source=AttemptSource.MANUAL, status=AttemptStatus.PENDING, filled_at=DIISI)
    )
    db_session.commit()
    cases = _load_case_grade_inputs(db_session, sesi.id)
    hasil = grade_session_data(
        session_id=sesi.id,
        cases_input=cases,
        triggered_by=pembuat.id,
        rubric_version="v1",
    )
    DbGradingStore(db_session).save_grading_run(hasil, cases)
    db_session.commit()

    # Act: laporan dibaca ulang dari grading run yang tersimpan, lewat instance baru
    tersimpan = DbGradingStore(db_session).get_latest_grading_run(sesi.id)

    # Assert
    assert tersimpan is not None
    percobaan = {a.attempt_id: a for c in tersimpan.cases for a in c.attempts}
    assert percobaan[target.id].status == AttemptStatus.SUCCEEDED
    assert percobaan[target.id].source == AttemptSource.MANUAL
    db_session.refresh(target)
    assert target.status == AttemptStatus.PENDING
