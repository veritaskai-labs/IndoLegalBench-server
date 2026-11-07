"""Unit test PBI-14 AC7: riwayat penilaian sesi (SCRUM-225).

Ringkasan satu run tidak membawa rincian kasus, dan daftar riwayat berurutan dari yang terbaru.
Penyimpanan hasil diganti MemoryGradingStore, jadi tidak ada yang bergantung pada tabel hasil.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User
from app.modules.runs.schemas import (
    CaseGradeResult,
    GradingRunSummary,
    ScorePair,
    SessionGradingResponse,
    ThreeScores,
)
from app.modules.runs.session_grade import (
    MemoryGradingStore,
    list_grading_runs,
    to_run_summary,
)
from app.shared.exceptions import NotFoundError
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

AWAL = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-riwayat@veritask.test", name="Admin Riwayat", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Riwayat")
    db_session.add(item)
    db_session.commit()
    return item


def _run(session_id, pembuat_id, **ubah) -> SessionGradingResponse:
    """Satu hasil penilaian lengkap dengan satu kasus, supaya terlihat apa yang dibuang."""
    isi = {
        "grading_run_id": uuid.uuid4(),
        "session_id": session_id,
        "session_label": "Sesi uji",
        "product_name": "Produk uji",
        "rubric_version": "v1",
        "kind": "initial",
        "status": "completed",
        "triggered_by": pembuat_id,
        "triggered_at": AWAL,
        "is_latest": True,
        "scores": ThreeScores(dev=ScorePair(), test=ScorePair(), combined=ScorePair()),
        "category_scores": [],
        "cases": [
            CaseGradeResult(
                snapshot_item_id=uuid.uuid4(),
                case_id=uuid.uuid4(),
                case_code="KASUS-01",
                title="Kasus satu",
            )
        ],
    }
    isi.update(ubah)
    return SessionGradingResponse(**isi)


# ---------------------------------------------------------------------------
# to_run_summary
# ---------------------------------------------------------------------------


def test_ringkasan_menyalin_identitas_versi_dan_skor_run(pembuat):
    # Arrange
    run = _run(uuid.uuid4(), pembuat.id, rubric_version="v2", kind="regrade")

    # Act
    ringkasan = to_run_summary(run)

    # Assert
    assert isinstance(ringkasan, GradingRunSummary)
    assert ringkasan.grading_run_id == run.grading_run_id
    assert ringkasan.rubric_version == "v2"
    assert ringkasan.kind == "regrade"
    assert ringkasan.status == "completed"
    assert ringkasan.triggered_by == pembuat.id
    assert ringkasan.triggered_at == AWAL
    assert ringkasan.is_latest is True
    assert ringkasan.scores == run.scores


def test_ringkasan_tidak_membawa_rincian_kasus(pembuat):
    # Arrange
    run = _run(uuid.uuid4(), pembuat.id)

    # Act
    isi = to_run_summary(run).model_dump()

    # Assert
    assert run.cases, "bahan uji harus punya kasus agar pembuangan terlihat"
    assert set(isi) == {
        "grading_run_id",
        "rubric_version",
        "kind",
        "status",
        "triggered_by",
        "triggered_at",
        "is_latest",
        "scores",
    }


def test_ringkasan_run_lama_tidak_bertanda_terbaru(pembuat):
    # Arrange
    run = _run(uuid.uuid4(), pembuat.id, is_latest=False)

    # Act
    ringkasan = to_run_summary(run)

    # Assert
    assert ringkasan.is_latest is False


# ---------------------------------------------------------------------------
# list_grading_runs
# ---------------------------------------------------------------------------


def test_riwayat_berurutan_dari_yang_terbaru(db_session, produk, pembuat):
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)
    store = MemoryGradingStore()
    pertama = _run(sesi.id, pembuat.id, triggered_at=AWAL)
    kedua = _run(sesi.id, pembuat.id, triggered_at=AWAL + timedelta(hours=1), kind="regrade")
    store.save_grading_run(pertama)
    store.save_grading_run(kedua)

    # Act
    riwayat = list_grading_runs(db_session, sesi.id, store=store)

    # Assert
    assert [r.grading_run_id for r in riwayat] == [kedua.grading_run_id, pertama.grading_run_id]
    assert [r.kind for r in riwayat] == ["regrade", "initial"]


def test_riwayat_hanya_satu_run_bertanda_terbaru(db_session, produk, pembuat):
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)
    store = MemoryGradingStore()
    store.save_grading_run(_run(sesi.id, pembuat.id, triggered_at=AWAL))
    store.save_grading_run(_run(sesi.id, pembuat.id, triggered_at=AWAL + timedelta(hours=1)))

    # Act
    riwayat = list_grading_runs(db_session, sesi.id, store=store)

    # Assert
    assert [r.is_latest for r in riwayat] == [True, False]


def test_riwayat_sesi_belum_pernah_dinilai_kosong(db_session, produk, pembuat):
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)

    # Act
    riwayat = list_grading_runs(db_session, sesi.id, store=MemoryGradingStore())

    # Assert
    assert riwayat == []


def test_riwayat_waktu_sama_mempertahankan_urutan_simpan(db_session, produk, pembuat):
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)
    store = MemoryGradingStore()
    a = _run(sesi.id, pembuat.id, triggered_at=AWAL, is_latest=False)
    b = _run(sesi.id, pembuat.id, triggered_at=AWAL, is_latest=False)
    c = _run(sesi.id, pembuat.id, triggered_at=AWAL, is_latest=False)
    for run in (a, b, c):
        store.save_grading_run(run)

    # Act
    riwayat = list_grading_runs(db_session, sesi.id, store=store)

    # Assert
    assert [r.grading_run_id for r in riwayat] == [
        a.grading_run_id,
        b.grading_run_id,
        c.grading_run_id,
    ]


def test_riwayat_sesi_tidak_ada_ditolak_404(db_session):
    # Arrange
    sesi_id = uuid.uuid4()

    # Act
    with pytest.raises(NotFoundError) as galat:
        list_grading_runs(db_session, sesi_id, store=MemoryGradingStore())

    # Assert
    assert galat.value.code == "MEASUREMENT_NOT_FOUND"


def test_riwayat_hanya_milik_sesi_yang_diminta(db_session, produk, pembuat):
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)
    lain = _sesi(db_session, produk, pembuat, label="Sesi lain")
    store = MemoryGradingStore()
    store.save_grading_run(_run(sesi.id, pembuat.id))
    store.save_grading_run(_run(lain.id, pembuat.id))

    # Act
    riwayat = list_grading_runs(db_session, sesi.id, store=store)

    # Assert
    assert len(riwayat) == 1
