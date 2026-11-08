"""Biaya dan anggaran bulanan penilai saat sesi dinilai. SCRUM-78 SCRUM-271.

PBI-14 AC6: biaya penilai dan anggaran bulanan adalah milik produk yang dipilih sebagai
penilai. Anggaran habis berarti penilai tidak dipanggil dan percobaan menjadi gangguan teknis.

Label: Positif = biaya tercatat dan penilai dipanggil, Negatif = anggaran habis,
Edge = anggaran habis di tengah sesi atau meter tidak dipasang.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.runs import session_grade
from app.modules.runs.judge import JudgePrices
from app.modules.runs.judge_budget import JudgeMeter, JudgeUsage
from app.modules.runs.models import AttemptStatus
from app.modules.runs.session_grade import (
    AttemptGradeInput,
    CaseGradeInput,
    grade_session,
)
from app.shared.security import Role
from tests.modules.runs.test_session_grade import (  # noqa: F401  (fixture)
    _BALASAN_PENILAI,
    _MockJudgeAsker,
    _setup_sesi_lengkap,
    pembuat,
    produk,
)

_RATE = Decimal("16000")
_HARGA = JudgePrices(Decimal("1"), Decimal("2"), _RATE)
# 25 token masuk dan 30 token keluar dari _MockJudgeAsker: (25 * 1 + 30 * 2) / 1e6 * 16000.
_BIAYA_PER_PANGGILAN = Decimal("1.36")


class _Rates:
    def usd_idr(self) -> Decimal:
        return _RATE


def _percobaan(nomor: int = 1) -> AttemptGradeInput:
    return AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=nomor,
        status=AttemptStatus.SUCCEEDED,
        answer_text="Jawaban provider.",
    )


def _kasus(*percobaan: AttemptGradeInput) -> CaseGradeInput:
    return CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="DEV-01",
        title="Kasus",
        question="Apa akibat hukumnya?",
        answer_criteria={"must_contain": ["badan hukum"], "conclusion": "sah"},
        attempts=list(percobaan),
    )


def _meter(sisa: str) -> JudgeMeter:
    return JudgeMeter(_HARGA, Decimal(sisa))


def test_anggaran_habis_menandai_percobaan_dan_tidak_memanggil_penilai():
    """Negatif: penilai tidak dipanggil dan percobaan menjadi gangguan teknis."""
    # Arrange
    penilai = MagicMock()
    percobaan = _percobaan()

    # Act
    session_grade._execute_judge_for_all_cases(penilai, None, [_kasus(percobaan)], _meter("0"))

    # Assert
    assert percobaan.judge_error == "judge_budget_exhausted"
    penilai.ask.assert_not_called()
    assert percobaan.judge_usage == {}


def test_biaya_tiap_panggilan_tercatat_per_jenis_dan_memotong_anggaran():
    """Positif: kualitas dan format masing-masing menyimpan token, biaya, dan kurs."""
    # Arrange
    percobaan = _percobaan()
    meter = _meter("100")

    # Act
    session_grade._execute_judge_for_all_cases(
        _MockJudgeAsker(text=_BALASAN_PENILAI), None, [_kasus(percobaan)], meter
    )

    # Assert
    harapan = JudgeUsage(25, 30, _BIAYA_PER_PANGGILAN, _RATE)
    assert percobaan.judge_usage == {"quality": harapan, "format": harapan}
    assert percobaan.judge_error != "judge_budget_exhausted"


def test_anggaran_habis_di_tengah_sesi_menandai_percobaan_berikutnya():
    """Edge: percobaan pertama menghabiskan sisa, percobaan kedua tidak dinilai."""
    # Arrange
    pertama, kedua = _percobaan(1), _percobaan(2)
    meter = _meter("2")
    penilai = _MockJudgeAsker(text=_BALASAN_PENILAI)

    # Act
    session_grade._execute_judge_for_all_cases(penilai, None, [_kasus(pertama, kedua)], meter)

    # Assert
    assert pertama.judge_error != "judge_budget_exhausted"
    assert set(pertama.judge_usage) == {"quality", "format"}
    assert kedua.judge_error == "judge_budget_exhausted"
    assert kedua.judge_usage == {}


def test_tanpa_meter_penilai_tetap_dipanggil_dan_biaya_kosong():
    """Edge: pemanggil yang menyuntikkan asker (tanpa kurs) tidak dibatasi dan tanpa biaya."""
    # Arrange
    percobaan = _percobaan()

    # Act
    session_grade._execute_judge_for_all_cases(
        _MockJudgeAsker(text=_BALASAN_PENILAI), None, [_kasus(percobaan)]
    )

    # Assert
    assert percobaan.judge_error != "judge_budget_exhausted"
    assert percobaan.judge_usage["quality"] == JudgeUsage(25, 30, None, None)


def test_meter_tidak_dibuka_tanpa_ikatan_penilai_atau_tanpa_kurs():
    """Edge: asker suntikan tidak punya ikatan, dan tanpa penyedia kurs biaya tidak dihitung."""
    # Arrange
    ikatan = SimpleNamespace(judge_provider_id=uuid.uuid4())

    # Act
    tanpa_ikatan = session_grade._open_judge_meter(MagicMock(), None, _Rates(), None)
    tanpa_kurs = session_grade._open_judge_meter(MagicMock(), ikatan, None, None)

    # Assert
    assert tanpa_ikatan is None
    assert tanpa_kurs is None


def test_meter_dibuka_dari_pengeluaran_produk_penilai_bulan_ini():
    """Positif: pengeluaran sejak awal bulan WIB produk penilai dikurangkan dari anggaran."""
    # Arrange
    id_produk = uuid.uuid4()
    ikatan = SimpleNamespace(
        judge_provider_id=id_produk,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000"),
    )
    sekarang = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)

    # Act
    with patch.object(session_grade, "CallLogRepository") as repo_cls:
        repo_cls.return_value.sum_cost_since.return_value = Decimal("1000")
        meter = session_grade._open_judge_meter(MagicMock(), ikatan, _Rates(), sekarang)

    # Assert
    awal_bulan_wib = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)
    repo_cls.return_value.sum_cost_since.assert_called_once_with(id_produk, awal_bulan_wib)
    assert meter.has_budget() is False
    assert meter.prices.usd_idr_rate == _RATE


def test_grade_sesi_dengan_anggaran_penilai_habis_menjadi_gangguan_teknis(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
    monkeypatch,
):
    """Negatif: sesi dinilai lewat produk penilai yang anggarannya habis tidak berskor."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.judge_provider_id = uuid.uuid4()
    db_session.commit()
    ikatan = SimpleNamespace(
        judge_provider_id=sesi.judge_provider_id,
        asker=MagicMock(),
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("0.01"),
    )
    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", lambda db, j: ikatan)
    with patch.object(session_grade, "CallLogRepository") as repo_cls:
        repo_cls.return_value.sum_cost_since.return_value = Decimal("5")

        # Act
        hasil = grade_session(
            db_session,
            sesi.id,
            actor_id=pembuat.id,
            actor_role=Role.ADMIN,
            rates=_Rates(),
        )

    # Assert
    percobaan = hasil.cases[0].attempts[0]
    assert percobaan.is_technical_disruption is True
    assert percobaan.score is None
    assert percobaan.explanation == "judge_budget_exhausted"
    ikatan.asker.ask.assert_not_called()


def _nilai_dengan_penilai_berbiaya(db_session, produk_, pembuat_, monkeypatch):
    sesi = _setup_sesi_lengkap(db_session, produk_, pembuat_)
    sesi.judge_provider_id = uuid.uuid4()
    db_session.commit()
    ikatan = SimpleNamespace(
        judge_provider_id=sesi.judge_provider_id,
        asker=_MockJudgeAsker(text=_BALASAN_PENILAI),
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000000"),
    )
    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", lambda db, j: ikatan)
    grade_session(db_session, sesi.id, actor_id=pembuat_.id, actor_role=Role.ADMIN, rates=_Rates())
    return sesi.judge_provider_id


def test_biaya_penilai_tersimpan_di_hasil_penilai(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
    monkeypatch,
):
    """Positif: tiap baris llm_judge_results menyimpan token, biaya, dan kurs panggilannya."""
    from sqlalchemy import select

    from app.modules.grading.models import LlmJudgeResult

    # Arrange / Act
    _nilai_dengan_penilai_berbiaya(db_session, produk, pembuat, monkeypatch)

    # Assert
    baris = db_session.scalars(select(LlmJudgeResult)).all()
    assert len(baris) >= 2
    for item in baris:
        assert item.input_tokens == 25
        assert item.output_tokens == 30
        assert item.cost_idr == _BIAYA_PER_PANGGILAN
        assert item.usd_idr_rate == _RATE


def test_pengeluaran_bulanan_produk_menghitung_biaya_penilai(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
    monkeypatch,
):
    """Positif: biaya penilai dijumlahkan dengan biaya panggilan produk yang sama."""
    from sqlalchemy import select

    from app.modules.grading.models import LlmJudgeResult
    from app.modules.runs.models import CallLog
    from app.modules.runs.repository import CallLogRepository

    # Arrange
    id_penilai = _nilai_dengan_penilai_berbiaya(db_session, produk, pembuat, monkeypatch)
    biaya_penilai = sum(
        (r.cost_idr or Decimal(0) for r in db_session.scalars(select(LlmJudgeResult)).all()),
        Decimal("0"),
    )
    sekarang = datetime.now(UTC)
    db_session.add(
        CallLog(
            attempt_id=uuid.uuid4(),
            product_id=id_penilai,
            started_at=sekarang,
            ended_at=sekarang,
            duration_ms=1,
            input_tokens=1,
            output_tokens=1,
            cost_idr=Decimal("10"),
            usd_idr_rate=_RATE,
            model_version="m",
            result="ok",
            failure_kind=None,
            raw={},
        )
    )
    db_session.commit()
    repo = CallLogRepository(db_session)
    awal = datetime(2000, 1, 1, tzinfo=UTC)

    # Act
    satu = repo.sum_cost_since(id_penilai, awal)
    banyak = repo.sum_cost_since_by_product([id_penilai, uuid.uuid4()], awal)
    sudah_lewat = repo.sum_cost_since(id_penilai, datetime(2999, 1, 1, tzinfo=UTC))
    produk_lain = repo.sum_cost_since(uuid.uuid4(), awal)

    # Assert
    assert biaya_penilai > 0
    assert satu == biaya_penilai + Decimal("10")
    assert banyak == {id_penilai: biaya_penilai + Decimal("10")}
    assert sudah_lewat == Decimal("0")
    assert produk_lain == Decimal("0")


def test_endpoint_grade_meneruskan_penyedia_kurs_ke_penilaian(
    as_role,
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
    monkeypatch,
):
    """Positif: router memberi grade_session penyedia kurs sehingga biaya penilai dihitung."""
    from app.main import app
    from app.modules.runs.dependencies import get_exchange_rate

    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.judge_provider_id = uuid.uuid4()
    db_session.commit()
    rates = _Rates()
    app.dependency_overrides[get_exchange_rate] = lambda: rates
    diterima = {}

    def tiruan(*args, **kwargs):
        diterima.update(kwargs)
        raise RuntimeError("berhenti di sini")

    monkeypatch.setattr("app.modules.runs.router.grade_session", tiruan)
    client = as_role(Role.ADMIN)

    # Act
    with pytest.raises(RuntimeError, match="berhenti di sini"):
        client.post(f"/measurements/{sesi.id}/grade", json={})

    # Assert
    assert diterima["rates"] is rates
