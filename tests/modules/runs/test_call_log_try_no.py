"""Upaya ke-n pada percobaan yang sama. SCRUM-77 SCRUM-218.

PBI-12 AC3 dan D2 call_logs: coba ulang manual menambah baris call_logs dengan
try_no + 1 pada attempt yang sama, tanpa menambah repetition_no. D4 menegaskan
tidak ada coba ulang otomatis, jadi upaya ke-2 dan seterusnya selalu datang
dari tombol pengguna (Coba ulang, atau Lanjutkan setelah restart di SCRUM-217);
itulah arti is_manual_retry.

Label: Positif = nomor upaya tercatat benar, Negatif = ditolak database,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.runs.call_log import CallLogService
from app.modules.runs.models import CallLog
from app.modules.runs.ports import AttemptRecord
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductInfo

PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
ATTEMPT_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ATTEMPT_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
JAM = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


class _Produk:
    def get(self, product_id):
        return ProductInfo(
            id=PRODUCT_ID,
            name="DeepSeek",
            measurement_type="automatic",
            is_active=True,
            provider_type="openai_compatible",
            model_name="deepseek-flash",
            monthly_budget_idr=Decimal("500000.00"),
            price_input_usd_per_mtok=Decimal("1"),
            price_output_usd_per_mtok=Decimal("1"),
        )


class _Kurs:
    def usd_idr(self):
        return Decimal("16000")


def _layanan(db_session) -> CallLogService:
    return CallLogService(
        products=_Produk(),
        repository=CallLogRepository(db_session),
        rates=_Kurs(),
        clock=lambda: JAM,
    )


def _upaya(attempt_id: uuid.UUID | None, **ubah) -> AttemptRecord:
    nilai = {
        "session_id": uuid.uuid4(),
        "case_index": 0,
        "repeat_index": 0,
        "prompt": "Apa dasar hukum PHK?",
        "text": "",
        "model_version": "deepseek-flash",
        "latency_ms": 10,
        "raw": {"error": "timeout"},
        "error": "timeout",
        "failure_kind": "no_response",
        "input_tokens": None,
        "output_tokens": None,
        "attempt_id": attempt_id,
    }
    nilai.update(ubah)
    return AttemptRecord(**nilai)


def _catat(layanan: CallLogService, record: AttemptRecord, menit: int = 0) -> CallLog:
    mulai = JAM + timedelta(minutes=menit)
    return layanan.record(PRODUCT_ID, record, mulai, mulai + timedelta(seconds=1))


def _baris(attempt_id: uuid.UUID, try_no: int) -> CallLog:
    return CallLog(
        attempt_id=attempt_id,
        product_id=PRODUCT_ID,
        started_at=JAM,
        ended_at=JAM,
        duration_ms=0,
        usd_idr_rate=Decimal("16000"),
        model_version="deepseek-flash",
        result="no_response",
        failure_kind="no_response",
        raw={},
        try_no=try_no,
    )


def test_upaya_pertama_bernomor_1_dan_bukan_coba_ulang(db_session):
    """Positif: panggilan awal sebuah attempt adalah upaya ke-1 (D2 "1 = awal")."""
    # Arrange
    layanan = _layanan(db_session)

    # Act
    baris = _catat(layanan, _upaya(ATTEMPT_A))

    # Assert
    assert baris.try_no == 1
    assert baris.is_manual_retry is False


def test_coba_ulang_pada_attempt_yang_sama_menambah_nomor_upaya(db_session):
    """Positif: AC3, coba ulang menambah baris baru try_no 2 di attempt yang sama."""
    # Arrange
    layanan = _layanan(db_session)
    _catat(layanan, _upaya(ATTEMPT_A))

    # Act
    ulang = _catat(layanan, _upaya(ATTEMPT_A, failure_kind=None, error=None), menit=5)

    # Assert
    assert ulang.attempt_id == ATTEMPT_A
    assert ulang.try_no == 2
    assert ulang.is_manual_retry is True


def test_nomor_upaya_dihitung_per_attempt(db_session):
    """Positif: upaya attempt lain tidak menggeser nomor upaya attempt ini."""
    # Arrange
    layanan = _layanan(db_session)
    _catat(layanan, _upaya(ATTEMPT_A))
    _catat(layanan, _upaya(ATTEMPT_A), menit=1)

    # Act
    baris = _catat(layanan, _upaya(ATTEMPT_B), menit=2)

    # Assert
    assert baris.try_no == 1
    assert baris.is_manual_retry is False


def test_upaya_tanpa_attempt_id_dianggap_upaya_pertama(db_session):
    """Edge: pemanggil lama tanpa attempt (port worker SCRUM-208) tetap upaya ke-1."""
    # Arrange
    layanan = _layanan(db_session)

    # Act
    baris = _catat(layanan, _upaya(None))

    # Assert
    assert baris.try_no == 1
    assert baris.is_manual_retry is False


def test_nomor_upaya_berikutnya_dari_repository(db_session):
    """Positif: next_try_no = upaya tertinggi + 1, atau 1 bila attempt belum pernah dipanggil."""
    # Arrange
    repository = CallLogRepository(db_session)
    db_session.add_all([_baris(ATTEMPT_A, 1), _baris(ATTEMPT_A, 2)])
    db_session.flush()

    # Act + Assert
    assert repository.next_try_no(ATTEMPT_A) == 3
    assert repository.next_try_no(ATTEMPT_B) == 1


def test_dua_baris_dengan_nomor_upaya_sama_ditolak(db_session):
    """Negatif: dua dispatch tidak boleh sama-sama tercatat sebagai upaya yang sama."""
    # Arrange
    db_session.add(_baris(ATTEMPT_A, 1))
    db_session.flush()
    db_session.add(_baris(ATTEMPT_A, 1))

    # Act + Assert
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nomor_upaya_nol_ditolak(db_session):
    """Negatif: try_no mulai dari 1 (D2), jadi 0 tidak punya arti."""
    # Arrange
    db_session.add(_baris(ATTEMPT_A, 0))

    # Act + Assert
    with pytest.raises(IntegrityError):
        db_session.flush()
