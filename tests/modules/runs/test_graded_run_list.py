"""Unit test list_graded_runs dengan GradingStore tiruan. PBI-22 AC9, SCRUM-176 SCRUM-296.

Store tiruan menggantikan adapter DB, jadi tes ini hanya memeriksa aturan service: peran yang
boleh membaca, sesi yang dilewati, pemetaan run ke baris, dan urutan terbaru di atas.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.auth.models import User
from app.modules.runs.graded_run_list import list_graded_runs
from app.modules.runs.models import SessionStatus
from app.modules.runs.schemas import (
    GradingJudge,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import GradingStore
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import _setup_sesi_lengkap

DASAR = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-daftar-run@veritask.test", name="Admin Daftar", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Daftar Run")
    db_session.add(item)
    db_session.commit()
    return item


def _run(
    sesi_id: uuid.UUID,
    menit: int,
    *,
    judge: GradingJudge | None = None,
    status: str = "completed",
    is_latest: bool = True,
) -> SessionGradingResponse:
    return SessionGradingResponse(
        grading_run_id=uuid.uuid4(),
        session_id=sesi_id,
        rubric_version="v1",
        kind="initial",
        status=status,
        triggered_by=uuid.uuid4(),
        triggered_at=DASAR + timedelta(minutes=menit),
        is_latest=is_latest,
        judge=judge,
        scores=ThreeScores(dev=ScorePair(), test=ScorePair(), combined=ScorePair()),
    )


def _store(runs_per_sesi: dict[uuid.UUID, list[SessionGradingResponse]]) -> MagicMock:
    store = MagicMock(spec=GradingStore)
    store.get_grading_runs.side_effect = lambda sesi_id: runs_per_sesi.get(sesi_id, [])
    return store


def test_run_dari_dua_sesi_diurutkan_terbaru_di_atas(db_session, produk, pembuat):
    """Positif: baris dari beberapa sesi digabung dan diurutkan menurut waktu penilaian."""
    # Arrange
    satu = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    dua = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    paling_lama = _run(satu.id, 0, is_latest=False)
    tengah = _run(dua.id, 5)
    paling_baru = _run(satu.id, 9)
    store = _store({satu.id: [paling_lama, paling_baru], dua.id: [tengah]})

    # Act
    baris = list_graded_runs(db_session, viewer_role=Role.ADMIN, store=store)

    # Assert
    assert [b.grading_run_id for b in baris] == [
        paling_baru.grading_run_id,
        tengah.grading_run_id,
        paling_lama.grading_run_id,
    ]


def test_baris_memetakan_judge_sesi_dan_produk_dari_run(db_session, produk, pembuat):
    """Positif: judge dan versi rubrik datang dari run, label dan produk dari sesi."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    judge = GradingJudge(
        judge_provider_id=uuid.uuid4(), name="Judge X", model_version="model-x", cost_idr=None
    )
    run = _run(sesi.id, 1, judge=judge)

    # Act
    (baris,) = list_graded_runs(db_session, viewer_role=Role.VIEWER, store=_store({sesi.id: [run]}))

    # Assert
    assert baris.session_id == sesi.id
    assert baris.label == sesi.label
    assert baris.product_name == produk.name
    assert baris.session_type == "automatic"
    assert baris.judge == judge
    assert baris.rubric_version == "v1"
    assert baris.graded_at == run.triggered_at


def test_sesi_tanpa_run_tidak_menghasilkan_baris(db_session, produk, pembuat):
    """Edge: sesi selesai yang belum pernah dinilai dilewati."""
    # Arrange
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    baris = list_graded_runs(db_session, viewer_role=Role.ADMIN, store=_store({}))

    # Assert
    assert baris == []


def test_sesi_berjalan_tidak_dibaca_dari_store(db_session, produk, pembuat):
    """Negatif: sesi yang belum selesai tidak ditanyakan ke store sama sekali."""
    # Arrange
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    store = _store({})

    # Act
    baris = list_graded_runs(db_session, viewer_role=Role.ADMIN, store=store)

    # Assert
    assert baris == []
    store.get_grading_runs.assert_not_called()


def test_peran_berupa_teks_diterima(db_session, produk, pembuat):
    """Edge: peran dari token bisa berupa string, bukan enum."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    store = _store({sesi.id: [_run(sesi.id, 1)]})

    # Act
    baris = list_graded_runs(db_session, viewer_role="contributor", store=store)

    # Assert
    assert len(baris) == 1


def test_developer_ai_ditolak_sebelum_store_dibaca(db_session, produk, pembuat):
    """Negatif: peran tanpa hak baca ditolak 403 dan tidak ada data yang dimuat."""
    # Arrange
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    store = _store({})

    # Act / Assert
    with pytest.raises(ForbiddenError, match="tidak berwenang"):
        list_graded_runs(db_session, viewer_role=Role.DEVELOPER_AI, store=store)
    store.get_grading_runs.assert_not_called()
