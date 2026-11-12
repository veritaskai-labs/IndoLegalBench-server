"""Perkiraan biaya penilaian satu sesi sebelum tombol Nilai. PBI-14 AC6, SCRUM-78 SCRUM-286.

Service memuat percobaan sesi, melewati gangguan teknis, menghitung token prompt kualitas dan
format, lalu menyerahkannya ke fungsi estimasi murni. Harga, kurs, dan sisa anggaran datang dari
JudgeMeter yang sama dengan penilaian sungguhan. Penyedia kurs dan penghitung token adalah
tiruan, dan penilai tidak pernah dipanggil.

Label: Positif = estimasi sesi otomatis dan manual, Negatif = ditolak dengan kode jelas,
Edge = semua percobaan gangguan teknis, anggaran tidak cukup, pengeluaran melebihi batas.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.grading.models import GradingRun
from app.modules.runs import grade_estimate_service
from app.modules.runs.call_log import month_start
from app.modules.runs.grade_estimate_service import estimate_session_grade
from app.modules.runs.models import Attempt, SessionStatus, SessionType
from app.modules.runs.schemas import GradeSessionRequest
from app.shared.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _manual, _otomatis
from tests.modules.runs.test_session_grade import (  # noqa: F401  (fixture)
    _setup_sesi_lengkap,
    pembuat,
    produk,
)

_SEKARANG = datetime(2026, 11, 12, tzinfo=UTC)
_RATE = Decimal("16000")
_TOKEN_PER_PROMPT = 10
_TOKEN_KELUAR = 100
# (10 * 1 + 100 * 2) / 1e6 * 16000 = 3.36 per panggilan, dua panggilan per percobaan.
_BIAYA_PER_PERCOBAAN = Decimal("6.72")


class _Rates:
    def usd_idr(self) -> Decimal:
        return _RATE


class _Counter:
    """Penghitung token tiruan: tetap 10 per prompt, dan mencatat apa yang dihitung."""

    def __init__(self) -> None:
        self.dihitung: list[tuple[str, str]] = []

    def count(self, text: str, provider_type: str) -> int:
        self.dihitung.append((text, provider_type))
        return _TOKEN_PER_PROMPT


@pytest.fixture
def penilai(db_session):
    item = _otomatis(
        "Penilai Estimasi",
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000"),
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def sesi(db_session, produk, pembuat, penilai):  # noqa: F811  (fixture)
    hasil = _setup_sesi_lengkap(db_session, produk, pembuat)
    hasil.judge_provider_id = penilai.id
    db_session.commit()
    return hasil


def _estimasi(db_session, sesi, *, payload=None, peran=Role.ADMIN, counter=None, sesi_id=None):
    return estimate_session_grade(
        db_session,
        sesi_id or sesi.id,
        payload,
        actor_role=peran,
        rates=_Rates(),
        counter=counter or _Counter(),
        output_tokens_per_call=_TOKEN_KELUAR,
        now=_SEKARANG,
    )


def _kosongkan_jawaban(db_session, sesi, jumlah: int) -> None:
    semua = db_session.scalars(select(Attempt).where(Attempt.session_id == sesi.id)).all()
    for percobaan in semua[:jumlah]:
        percobaan.answer_text = ""
    db_session.commit()


def test_estimasi_sesi_otomatis_menghitung_percobaan_panggilan_token_dan_biaya(
    db_session, sesi, penilai
):
    """Positif: dua percobaan layak, empat panggilan, dan biaya dari harga produk penilai."""
    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.judge_provider_id == penilai.id
    assert hasil.rubric_version == "v1"
    assert hasil.attempts_to_grade == 2
    assert hasil.judge_calls == 4
    assert hasil.estimated_input_tokens == 4 * _TOKEN_PER_PROMPT
    assert hasil.estimated_output_tokens == 4 * _TOKEN_KELUAR
    assert hasil.estimated_cost_idr == 2 * _BIAYA_PER_PERCOBAAN
    assert hasil.usd_idr_rate == _RATE
    assert hasil.remaining_budget_idr == Decimal("1000.00")
    assert hasil.budget_sufficient is True


def test_estimasi_sesi_manual_lengkap_sama_dengan_sesi_otomatis(db_session, sesi):
    """Positif: sesi manual yang sudah lengkap memakai alur dan rumus yang sama."""
    # Arrange
    sesi.session_type = SessionType.MANUAL
    sesi.status = SessionStatus.COMPLETE
    db_session.commit()

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.attempts_to_grade == 2
    assert hasil.estimated_cost_idr == 2 * _BIAYA_PER_PERCOBAAN


def test_token_dihitung_dari_prompt_kualitas_dan_format_dengan_jenis_penyedia_judge(
    db_session, sesi
):
    """Positif: yang dihitung prompt sungguhan ke penilai, dan jawabannya ikut di dalamnya."""
    # Arrange
    counter = _Counter()

    # Act
    _estimasi(db_session, sesi, counter=counter)

    # Assert
    assert len(counter.dihitung) == 4
    assert {jenis for _, jenis in counter.dihitung} == {"openai_compatible"}
    assert all("Provider answer:\n" in teks for teks, _ in counter.dihitung)
    assert sum(teks.startswith("Score the quality") for teks, _ in counter.dihitung) == 2
    assert sum(teks.startswith("Score whether") for teks, _ in counter.dihitung) == 2


def test_gangguan_teknis_tidak_dihitung_karena_tidak_dikirim_ke_penilai(db_session, sesi):
    """Edge: jawaban kosong dilewati, jadi hanya satu percobaan yang diperkirakan."""
    # Arrange
    _kosongkan_jawaban(db_session, sesi, jumlah=1)

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.attempts_to_grade == 1
    assert hasil.judge_calls == 2
    assert hasil.estimated_cost_idr == _BIAYA_PER_PERCOBAAN


def test_semua_percobaan_gangguan_teknis_estimasi_nol_bukan_galat(db_session, sesi):
    """Edge: tidak ada panggilan penilai, biaya nol, dan anggaran dianggap cukup."""
    # Arrange
    _kosongkan_jawaban(db_session, sesi, jumlah=2)

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert (hasil.attempts_to_grade, hasil.judge_calls) == (0, 0)
    assert hasil.estimated_cost_idr == Decimal("0.00")
    assert hasil.budget_sufficient is True


def test_anggaran_tidak_cukup_dilaporkan_bukan_dilempar(db_session, sesi, penilai):
    """Edge: biaya 13.44 melebihi sisa 10, hasilnya tetap 200 dengan budget_sufficient false."""
    # Arrange
    penilai.monthly_budget_idr = Decimal("10")
    db_session.commit()

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.remaining_budget_idr == Decimal("10.00")
    assert hasil.estimated_cost_idr == Decimal("13.44")
    assert hasil.budget_sufficient is False


def test_sisa_anggaran_dikurangi_pengeluaran_bulan_ini(db_session, sesi, monkeypatch):
    """Positif: sisa adalah batas bulanan dikurangi biaya yang sudah tercatat bulan ini."""
    # Arrange
    dipakai = {}

    def _pengeluaran(_self, product_id, start):
        dipakai["start"] = start
        return Decimal("990")

    monkeypatch.setattr(grade_estimate_service.CallLogRepository, "sum_cost_since", _pengeluaran)

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.remaining_budget_idr == Decimal("10.00")
    assert hasil.budget_sufficient is False
    assert dipakai["start"] == month_start(_SEKARANG)


def test_pengeluaran_melebihi_batas_sisa_dilaporkan_nol_bukan_negatif(
    db_session, sesi, monkeypatch
):
    """Edge: penilaian terakhir bisa melampaui batas, tetapi sisa yang ditampilkan nol."""
    # Arrange
    monkeypatch.setattr(
        grade_estimate_service.CallLogRepository,
        "sum_cost_since",
        lambda _self, product_id, start: Decimal("2000"),
    )

    # Act
    hasil = _estimasi(db_session, sesi)

    # Assert
    assert hasil.remaining_budget_idr == Decimal("0.00")
    assert hasil.budget_sufficient is False


@pytest.mark.parametrize("kolom", ["price_input_usd_per_mtok", "price_output_usd_per_mtok"])
def test_judge_tanpa_harga_lengkap_ditolak_422_dengan_kode_jelas(db_session, sesi, penilai, kolom):
    """Negatif: bukan 500, supaya Admin tahu harus melengkapi harga produk penilai."""
    # Arrange
    setattr(penilai, kolom, None)
    db_session.commit()

    # Act
    with pytest.raises(ValidationError) as galat:
        _estimasi(db_session, sesi)

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "JUDGE_PRICING_MISSING"


def test_judge_pilihan_admin_dipakai_bukan_judge_sesi(db_session, sesi):
    """Positif: harga dan anggaran mengikuti judge yang dipilih di permintaan."""
    # Arrange
    lain = _otomatis(
        "Penilai Lain",
        price_input_usd_per_mtok=Decimal("0"),
        price_output_usd_per_mtok=Decimal("0"),
        monthly_budget_idr=Decimal("50"),
    )
    db_session.add(lain)
    db_session.commit()

    # Act
    hasil = _estimasi(db_session, sesi, payload=GradeSessionRequest(judge_provider_id=lain.id))

    # Assert
    assert hasil.judge_provider_id == lain.id
    assert hasil.estimated_cost_idr == Decimal("0.00")
    assert hasil.remaining_budget_idr == Decimal("50.00")


@pytest.mark.parametrize("jenis", ["manual", "nonaktif", "tidak_ada"])
def test_judge_pilihan_tidak_layak_ditolak_422(db_session, sesi, jenis):
    """Negatif: hanya produk AI otomatis yang aktif, aturan yang sama dengan tombol Nilai."""
    # Arrange
    if jenis == "tidak_ada":
        judge_id = uuid.uuid4()
    else:
        produk_lain = (
            _manual("Bukan Judge") if jenis == "manual" else _otomatis("Mati", is_active=False)
        )
        db_session.add(produk_lain)
        db_session.commit()
        judge_id = produk_lain.id

    # Act
    with pytest.raises(ValidationError) as galat:
        _estimasi(db_session, sesi, payload=GradeSessionRequest(judge_provider_id=judge_id))

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "JUDGE_NOT_ELIGIBLE"


def test_tanpa_judge_sesi_dan_tanpa_pilihan_ditolak_422(db_session, sesi):
    """Negatif: sesi lama tanpa penilai dan tanpa pilihan Admin tidak punya harga untuk dihitung."""
    # Arrange
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    with pytest.raises(ValidationError) as galat:
        _estimasi(db_session, sesi)

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "JUDGE_NOT_CONFIGURED"


def test_versi_rubrik_tidak_ada_ditolak_404(db_session, sesi):
    """Negatif: versi yang diminta divalidasi sama seperti saat menilai."""
    # Act
    with pytest.raises(NotFoundError) as galat:
        _estimasi(db_session, sesi, payload=GradeSessionRequest(rubric_version="v99"))

    # Assert
    assert galat.value.code == "RUBRIC_VERSION_NOT_FOUND"


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI, Role.CONTRIBUTOR])
def test_selain_admin_ditolak_403(db_session, sesi, peran):
    """Negatif: biaya penilaian hanya untuk Admin yang akan menekan Nilai."""
    # Act
    with pytest.raises(ForbiddenError) as galat:
        _estimasi(db_session, sesi, peran=peran)

    # Assert
    assert galat.value.status_code == 403


def test_sesi_tidak_ada_ditolak_404(db_session, sesi):
    """Negatif: id sesi yang tidak ada."""
    # Act
    with pytest.raises(NotFoundError) as galat:
        _estimasi(db_session, sesi, sesi_id=uuid.uuid4())

    # Assert
    assert galat.value.code == "MEASUREMENT_NOT_FOUND"


def test_sesi_diarsipkan_ditolak_409(db_session, sesi, pembuat):  # noqa: F811
    """Negatif: sesi arsip tidak dinilai, jadi tidak ada gunanya diperkirakan."""
    # Arrange
    sesi.archived_at = datetime.now(UTC)
    sesi.archived_by = pembuat.id
    db_session.commit()

    # Act
    with pytest.raises(ConflictError) as galat:
        _estimasi(db_session, sesi)

    # Assert
    assert galat.value.code == "SESSION_ARCHIVED"


def test_sesi_otomatis_belum_dimulai_ditolak_409(db_session, sesi):
    """Negatif: sesi yang masih antre belum bisa dinilai (aturan D4)."""
    # Arrange
    sesi.status = SessionStatus.QUEUED
    db_session.commit()

    # Act
    with pytest.raises(ConflictError) as galat:
        _estimasi(db_session, sesi)

    # Assert
    assert galat.value.code == "SESSION_NOT_GRADABLE"


def test_sesi_manual_belum_lengkap_ditolak_409(db_session, sesi):
    """Negatif: tombol Nilai sesi manual baru ada setelah semua jawaban terisi (D7)."""
    # Arrange
    sesi.session_type = SessionType.MANUAL
    sesi.status = SessionStatus.PARTIALLY_FILLED
    db_session.commit()

    # Act
    with pytest.raises(ConflictError) as galat:
        _estimasi(db_session, sesi)

    # Assert
    assert galat.value.code == "NOT_COMPLETE"


def test_estimasi_tidak_menyimpan_run_penilaian(db_session, sesi):
    """Positif: perkiraan tidak punya efek samping, tidak ada run penilaian yang dibuat."""
    # Act
    _estimasi(db_session, sesi)

    # Assert
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == 0
