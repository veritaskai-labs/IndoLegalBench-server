"""Aturan kelayakan sesi dan syarat perbandingan. PBI-15 SCRUM-79 SCRUM-301.

AC1, AC2, AC3, AC4, AC5, AC13. Murni, tanpa database.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.reports.comparison_rules import (
    JUDGE_MODEL,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    RUBRIC_VERSION,
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_FINISHED,
    SESSION_NOT_GRADED,
    SESSION_RUN_FAILED,
    SUITE_VERSION,
    TRIAL_SESSION,
    CaseRef,
    SessionFacts,
    comparison_rejections,
    eligibility_problems,
    ensure_eligible,
    is_eligible,
    plan_comparison,
    session_markers,
    shared_cases,
)
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from app.shared.exceptions import ConflictError

RUBRIK = uuid.UUID("11111111-1111-1111-1111-111111111111")
PENILAI = uuid.UUID("22222222-2222-2222-2222-222222222222")
SUITE = uuid.UUID("33333333-3333-3333-3333-333333333333")
AWAL = datetime(2026, 11, 1, tzinfo=UTC)


def _kasus(nomor: int, *, versi: int = 1) -> CaseRef:
    return CaseRef(
        case_id=uuid.UUID(f"{nomor:032x}"),
        case_version_id=uuid.UUID(f"{nomor:016x}{versi:016x}"),
        case_code=f"CASE-{nomor:03d}",
    )


def _sesi(**ubah) -> SessionFacts:
    bawaan = {
        "session_id": uuid.uuid4(),
        "product_id": uuid.uuid4(),
        "product_name": "Produk",
        "session_type": SessionType.AUTOMATIC,
        "purpose": SessionPurpose.OFFICIAL,
        "status": SessionStatus.FINISHED,
        "accepted_as_is_at": None,
        "rubric_version_id": RUBRIK,
        "judge_provider_id": PENILAI,
        "suite_ids": frozenset({SUITE}),
        "model_version": "v1",
        "repetitions": 3,
        "answer_date_from": None,
        "answer_date_to": None,
        "scored_cases": (_kasus(1), _kasus(2)),
    }
    return SessionFacts(**{**bawaan, **ubah})


# AC2, AC4: kelayakan sesi.


def test_sesi_selesai_dan_dinilai_layak():
    assert eligibility_problems(_sesi()) == ()
    assert is_eligible(_sesi()) is True


def test_sesi_dengan_kegagalan_hanya_layak_setelah_diterima_apa_adanya():
    gagal = _sesi(status=SessionStatus.FINISHED_WITH_FAILURES)

    assert eligibility_problems(gagal) == (SESSION_NOT_ACCEPTED_AS_IS,)
    assert (
        eligibility_problems(
            _sesi(status=SessionStatus.FINISHED_WITH_FAILURES, accepted_as_is_at=AWAL)
        )
        == ()
    )


def test_sesi_dibatalkan_dan_sesi_run_gagal_tidak_layak():
    assert eligibility_problems(_sesi(status=SessionStatus.CANCELLED)) == (SESSION_CANCELLED,)
    assert eligibility_problems(_sesi(status=SessionStatus.FAILED)) == (SESSION_RUN_FAILED,)


@pytest.mark.parametrize(
    "status", [SessionStatus.QUEUED, SessionStatus.RUNNING, SessionStatus.PAUSED]
)
def test_sesi_yang_belum_berakhir_tidak_layak(status):
    assert eligibility_problems(_sesi(status=status)) == (SESSION_NOT_FINISHED,)


def test_sesi_manual_layak_hanya_setelah_selesai_dan_dinilai():
    manual = {"session_type": SessionType.MANUAL}

    assert eligibility_problems(_sesi(status=SessionStatus.FINALIZED, **manual)) == ()
    assert eligibility_problems(_sesi(status=SessionStatus.GRADED, **manual)) == (
        SESSION_NOT_FINISHED,
    )
    assert eligibility_problems(_sesi(status=SessionStatus.COMPLETE, **manual)) == (
        SESSION_NOT_FINISHED,
    )


def test_sesi_selesai_tanpa_penilaian_belum_layak():
    assert eligibility_problems(_sesi(rubric_version_id=None)) == (SESSION_NOT_GRADED,)


def test_ensure_eligible_menolak_sesi_tidak_layak_dengan_kode_alasannya():
    ensure_eligible(_sesi())

    with pytest.raises(ConflictError) as galat:
        ensure_eligible(_sesi(status=SessionStatus.CANCELLED))

    assert galat.value.code == SESSION_CANCELLED


def test_sesi_uji_coba_layak_tetapi_wajib_diberi_tanda():
    uji_coba = _sesi(purpose=SessionPurpose.TRIAL)

    assert eligibility_problems(uji_coba) == ()
    assert TRIAL_SESSION in session_markers(uji_coba)
    assert TRIAL_SESSION not in session_markers(_sesi())


# AC5: rentang tanggal jawaban sesi manual lebih dari 7 hari.


def test_rentang_tanggal_jawaban_manual_lebih_dari_tujuh_hari_diberi_tanda():
    def manual(hari: int) -> SessionFacts:
        return _sesi(
            session_type=SessionType.MANUAL,
            status=SessionStatus.FINALIZED,
            answer_date_from=AWAL,
            answer_date_to=AWAL + timedelta(days=hari),
        )

    assert MANUAL_ANSWER_RANGE_OVER_LIMIT in session_markers(manual(8))
    assert MANUAL_ANSWER_RANGE_OVER_LIMIT not in session_markers(manual(7))


# AC13: syarat perbandingan, ditolak dengan alasan per syarat.


def test_semua_syarat_sama_tidak_menghasilkan_penolakan():
    assert comparison_rejections((_sesi(), _sesi())) == ()


def test_versi_rubrik_berbeda_ditolak():
    lain = _sesi(rubric_version_id=uuid.uuid4())

    assert [tolak.requirement for tolak in comparison_rejections((_sesi(), lain))] == [
        RUBRIC_VERSION
    ]


def test_model_penilai_berbeda_ditolak():
    lain = _sesi(judge_provider_id=uuid.uuid4())

    assert [tolak.requirement for tolak in comparison_rejections((_sesi(), lain))] == [JUDGE_MODEL]


def test_suite_berbeda_ditolak():
    lain = _sesi(suite_ids=frozenset({uuid.uuid4()}))

    assert [tolak.requirement for tolak in comparison_rejections((_sesi(), lain))] == [
        SUITE_VERSION
    ]


def test_kasus_yang_sama_pada_versi_berbeda_adalah_beda_versi_suite():
    lain = _sesi(scored_cases=(_kasus(1, versi=2), _kasus(2)))

    tolakan = comparison_rejections((_sesi(), lain))

    assert [tolak.requirement for tolak in tolakan] == [SUITE_VERSION]
    assert "CASE-001" in tolakan[0].message


def test_setiap_syarat_yang_gagal_punya_alasannya_sendiri():
    lain = _sesi(
        rubric_version_id=uuid.uuid4(),
        judge_provider_id=uuid.uuid4(),
        suite_ids=frozenset({uuid.uuid4()}),
    )

    tolakan = comparison_rejections((_sesi(), lain))

    assert [tolak.requirement for tolak in tolakan] == [RUBRIC_VERSION, JUDGE_MODEL, SUITE_VERSION]
    assert all(tolak.message for tolak in tolakan)


# AC3, AC5: penanda perbandingan.


def test_versi_model_berbeda_dan_jumlah_percobaan_berbeda_diberi_tanda():
    sesi = (_sesi(), _sesi(model_version="v2", repetitions=5))

    rencana = plan_comparison(sesi)

    assert MODEL_VERSION_DIFFERS in rencana.markers
    assert REPETITIONS_DIFFER in rencana.markers


def test_versi_model_dan_jumlah_percobaan_sama_tidak_diberi_tanda():
    rencana = plan_comparison((_sesi(), _sesi()))

    assert MODEL_VERSION_DIFFERS not in rencana.markers
    assert REPETITIONS_DIFFER not in rencana.markers


# AC1: irisan kasus.


def test_irisan_hanya_kasus_yang_dijawab_semua_produk():
    satu = _sesi(scored_cases=(_kasus(1), _kasus(2), _kasus(3)))
    dua = _sesi(scored_cases=(_kasus(1), _kasus(2)))
    tiga = _sesi(scored_cases=(_kasus(2), _kasus(3)))

    assert [kasus.case_code for kasus in shared_cases((satu, dua, tiga))] == ["CASE-002"]


def test_irisan_satu_kasus_tetap_dibandingkan_tanpa_batas_minimum():
    satu = _sesi(scored_cases=(_kasus(1), _kasus(2)))
    dua = _sesi(scored_cases=(_kasus(1),))

    rencana = plan_comparison((satu, dua))

    assert rencana.comparable is True
    assert [kasus.case_code for kasus in rencana.shared_cases] == ["CASE-001"]
    assert rencana.warning is None


def test_irisan_kosong_menghasilkan_peringatan_tanpa_angka():
    satu = _sesi(scored_cases=(_kasus(1),))
    dua = _sesi(scored_cases=(_kasus(2),))

    rencana = plan_comparison((satu, dua))

    assert rencana.shared_cases == ()
    assert rencana.warning == NO_SHARED_CASES
    assert rencana.comparable is False


# Rencana utuh: syarat gagal atau sesi tidak layak menghentikan perbandingan.


def test_syarat_gagal_menghentikan_perbandingan_dan_menjelaskan_alasannya():
    rencana = plan_comparison((_sesi(), _sesi(rubric_version_id=uuid.uuid4())))

    assert rencana.comparable is False
    assert rencana.shared_cases == ()
    assert [tolak.requirement for tolak in rencana.rejections] == [RUBRIC_VERSION]


def test_sesi_tidak_layak_dilaporkan_per_sesi_dan_menghentikan_perbandingan():
    batal = _sesi(status=SessionStatus.CANCELLED)

    rencana = plan_comparison((_sesi(), batal))

    assert rencana.comparable is False
    assert rencana.ineligible[batal.session_id] == (SESSION_CANCELLED,)
