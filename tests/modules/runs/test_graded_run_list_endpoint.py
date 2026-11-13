"""Daftar laporan satu baris per grading run. PBI-14 AC8, PBI-22 AC9, SCRUM-176 SCRUM-296.

Feedback UAT klien 9 Nov (K-21): setiap hasil penilaian baru muncul sebagai entri sendiri di
halaman Laporan, lengkap dengan LLM judge dan versi rubrik, dan nilai ulang tidak menghapus
baris lama. Hak akses tetap seperti SCRUM-276.

Isolasi: build_judge diganti lewat monkeypatch, karena penilai sungguhan butuh kredensial
dan memakai token berbayar. Kurs dipasang tetap lewat dependency override.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.auth.models import User
from app.modules.grading.models import GradingRun
from app.modules.runs.dependencies import get_exchange_rate
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import (
    _BALASAN_PENILAI,
    _MockJudgeAsker,
    _setup_sesi_lengkap,
    pasang_penilai_rusak,
)

DAFTAR = "/results/sessions"


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-laporan-run@veritask.test", name="Admin Laporan", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Diukur Laporan")
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def penilai(db_session):
    """Dua produk otomatis aktif yang boleh dipilih sebagai LLM judge."""
    a = _otomatis("Penilai Laporan A")
    b = _otomatis("Penilai Laporan B")
    db_session.add_all([a, b])
    db_session.commit()
    return SimpleNamespace(a=a, b=b)


class _PenilaiBerversi(_MockJudgeAsker):
    """Penilai tiruan yang melaporkan versi modelnya, seperti ProviderResponse asli."""

    def __init__(self, model_version: str) -> None:
        super().__init__(text=_BALASAN_PENILAI)
        self.model_version = model_version


def _ikatan(judge_id: uuid.UUID, asker: object) -> SimpleNamespace:
    return SimpleNamespace(
        judge_provider_id=judge_id,
        asker=asker,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000000"),
    )


@pytest.fixture
def pasang_penilai(db_session, penilai, monkeypatch):
    """Sesi memakai Penilai A; build_judge mengembalikan asker milik id yang diminta."""

    def pasang(sesi):
        sesi.judge_provider_id = penilai.a.id
        db_session.commit()
        ikatan = {
            penilai.a.id: _ikatan(penilai.a.id, _PenilaiBerversi("model-a-2026")),
            penilai.b.id: _ikatan(penilai.b.id, _PenilaiBerversi("model-b-2026")),
        }
        monkeypatch.setattr(
            "app.modules.providers.judge_service.build_judge", lambda db, j: ikatan[j]
        )
        app.dependency_overrides[get_exchange_rate] = lambda: ConfiguredExchangeRate(
            Decimal("16000")
        )

    return pasang


def _nilai(client, sesi_id: uuid.UUID, judge_id: uuid.UUID | None = None) -> dict:
    body = {"judge_provider_id": str(judge_id)} if judge_id else {}
    res = client.post(f"/measurements/{sesi_id}/grade", json=body)
    assert res.status_code == 200
    return res.json()


def test_dua_run_satu_sesi_menjadi_dua_baris(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: nilai ulang menambah baris baru, baris lama tetap ada (AC9)."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    pertama = _nilai(admin, sesi.id, penilai.a.id)
    kedua = _nilai(admin, sesi.id, penilai.b.id)

    # Act
    res = admin.get(DAFTAR)

    # Assert
    assert res.status_code == 200
    baris = res.json()
    assert [b["grading_run_id"] for b in baris] == [
        kedua["grading_run_id"],
        pertama["grading_run_id"],
    ]
    assert {b["session_id"] for b in baris} == {str(sesi.id)}


def test_baris_membawa_metadata_judge_rubrik_dan_skor_run_itu(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: judge (produk + model), versi rubrik, produk diukur, dan skor sesuai run-nya."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    pertama = _nilai(admin, sesi.id, penilai.a.id)
    kedua = _nilai(admin, sesi.id, penilai.b.id)

    # Act
    baris = {b["grading_run_id"]: b for b in admin.get(DAFTAR).json()}

    # Assert
    satu, dua = baris[pertama["grading_run_id"]], baris[kedua["grading_run_id"]]
    assert satu["judge"]["name"] == "Penilai Laporan A"
    assert satu["judge"]["model_version"] == "model-a-2026"
    assert dua["judge"]["name"] == "Penilai Laporan B"
    assert dua["judge"]["model_version"] == "model-b-2026"
    assert satu["rubric_version"] == "v1"
    assert satu["label"] == sesi.label
    assert satu["product_name"] == produk.name
    assert satu["session_type"] == "automatic"
    assert satu["status"] == "completed"
    assert satu["scores"] == pertama["scores"]
    assert dua["scores"] == kedua["scores"]
    assert datetime.fromisoformat(satu["graded_at"]) == datetime.fromisoformat(
        pertama["triggered_at"]
    )


def test_hanya_run_terbaru_bertanda_latest(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Edge: dua run satu sesi, hanya yang terakhir is_latest."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    _nilai(admin, sesi.id, penilai.a.id)
    kedua = _nilai(admin, sesi.id, penilai.b.id)

    # Act
    baris = admin.get(DAFTAR).json()

    # Assert
    assert [b["is_latest"] for b in baris] == [True, False]
    assert baris[0]["grading_run_id"] == kedua["grading_run_id"]


def test_sesi_manual_ditandai_manual(as_role, db_session, produk, pembuat, pasang_penilai):
    """Positif: kolom jenis membedakan sesi otomatis dan manual."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    _nilai(admin, sesi.id)
    sesi.session_type = SessionType.MANUAL
    db_session.commit()

    # Act
    baris = admin.get(DAFTAR).json()

    # Assert
    assert [b["session_type"] for b in baris] == ["manual"]


def test_run_gagal_tetap_tampil_dengan_status_failed(
    as_role, db_session, produk, pembuat, monkeypatch
):
    """Edge: run tanpa satu pun skor tetap menjadi baris, supaya Admin melihat percobaannya."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai_rusak(db_session, sesi, monkeypatch)
    admin = as_role(Role.ADMIN)
    gagal = _nilai(admin, sesi.id)

    # Act
    baris = admin.get(DAFTAR).json()

    # Assert
    assert gagal["status"] == "failed"
    assert [(b["grading_run_id"], b["status"]) for b in baris] == [
        (gagal["grading_run_id"], "failed")
    ]


def test_run_lama_tanpa_judge_tetap_tampil_dengan_judge_null(
    as_role, db_session, produk, pembuat, pasang_penilai
):
    """Edge: run dari sebelum SCRUM-280 tidak punya judge dan tidak boleh membuat daftar error."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    _nilai(admin, sesi.id)
    run = db_session.scalars(select(GradingRun).where(GradingRun.session_id == sesi.id)).one()
    run.judge_provider_id = None
    db_session.commit()

    # Act
    baris = admin.get(DAFTAR).json()

    # Assert
    assert len(baris) == 1
    assert baris[0]["judge"] is None


def test_sesi_belum_dinilai_tidak_menjadi_baris(as_role, db_session, produk, pembuat):
    """Negatif: laporan hanya memuat hasil penilaian, bukan sesi yang belum dinilai."""
    # Arrange
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    res = as_role(Role.ADMIN).get(DAFTAR)

    # Assert
    assert res.status_code == 200
    assert res.json() == []


def test_sesi_berjalan_tidak_menjadi_baris(as_role, db_session, produk, pembuat):
    """Negatif: sesi yang belum selesai tidak muncul di laporan."""
    # Arrange
    _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.RUNNING)

    # Act
    res = as_role(Role.ADMIN).get(DAFTAR)

    # Assert
    assert res.status_code == 200
    assert res.json() == []


def test_sesi_diarsipkan_tidak_menjadi_baris(as_role, db_session, produk, pembuat, pasang_penilai):
    """Negatif: sesi yang diarsipkan tidak muncul di laporan, walau sudah dinilai."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    _nilai(admin, sesi.id)
    sesi.archived_at = datetime.now(UTC)
    sesi.archived_by = pembuat.id
    db_session.commit()

    # Act
    res = admin.get(DAFTAR)

    # Assert
    assert res.json() == []


@pytest.mark.parametrize("peran", [Role.ADMIN, Role.VIEWER, Role.CONTRIBUTOR])
def test_peran_pembaca_melihat_semua_baris_run(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai, peran
):
    """Positif: Admin, Viewer, dan Kontributor membaca daftar yang sama (SCRUM-276)."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    _nilai(admin, sesi.id, penilai.a.id)
    _nilai(admin, sesi.id, penilai.b.id)

    # Act
    res = as_role(peran).get(DAFTAR)

    # Assert
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_developer_ai_tetap_ditolak_403(as_role, db_session, produk, pembuat, pasang_penilai):
    """Negatif: Developer AI tidak melihat laporan, aturan akses tidak berubah."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    _nilai(as_role(Role.ADMIN), sesi.id)

    # Act
    res = as_role(Role.DEVELOPER_AI).get(DAFTAR)

    # Assert
    assert res.status_code == 403
