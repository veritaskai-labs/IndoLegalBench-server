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
