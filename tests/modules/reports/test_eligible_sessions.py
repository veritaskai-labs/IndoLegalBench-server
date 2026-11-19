"""Daftar sesi yang layak jadi dasar laporan. PBI-15 SCRUM-79 SCRUM-313.

Murni, tanpa database: pembaca sesi diganti pembaca palsu. Yang diuji: tiap sesi diberi
eligible beserta alasannya, urutan dari pembaca dipertahankan, dan filter jenis pengukuran.
"""

import pytest

from app.modules.reports.comparison_rules import (
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_GRADED,
)
from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.models import MeasurementFilter
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from tests.modules.reports.test_report_generation import AWAL, PembacaPalsu, _nilai, _rekaman


def _daftar(*rekaman, **ubah):
    return EligibleSessionLister(PembacaPalsu(*rekaman)).list(**ubah)


def test_a_finished_and_graded_session_is_eligible_without_reasons():
    # Arrange
    layak = _rekaman("Produk A", [_nilai(1, "0.5", "0.5")])

    # Act
    (item,) = _daftar(layak)

    # Assert
    assert item.session_id == layak.facts.session_id
    assert item.product_name == "Produk A"
    assert item.label == "Sesi Produk A"
    assert item.product_id == layak.facts.product_id
    assert item.judge_model_version == "judge-1"
    assert item.created_at == AWAL
    assert item.session_type is SessionType.AUTOMATIC
    assert item.purpose is SessionPurpose.OFFICIAL
    assert item.status is SessionStatus.FINISHED
    assert item.eligible is True
    assert item.reasons == ()


def test_a_cancelled_session_is_not_eligible_and_says_why():
    # Arrange
    batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

    # Act
    (item,) = _daftar(batal)

    # Assert
    assert item.eligible is False
    assert [alasan.code for alasan in item.reasons] == [SESSION_CANCELLED]
    assert item.reasons[0].message


def test_an_ungraded_session_is_not_eligible():
    # Arrange
    belum = _rekaman("Belum", [_nilai(1, "0.5", "0.5")], rubrik=None)

    # Act
    (item,) = _daftar(belum)

    # Assert
    assert item.eligible is False
    assert [alasan.code for alasan in item.reasons] == [SESSION_NOT_GRADED]


def test_every_reason_is_listed_when_a_session_has_several():
    # Arrange: selesai dengan kegagalan, belum diterima, dan belum dinilai.
    banyak = _rekaman(
        "Banyak",
        [_nilai(1, "0.5", "0.5")],
        status=SessionStatus.FINISHED_WITH_FAILURES,
        rubrik=None,
    )

    # Act
    (item,) = _daftar(banyak)

    # Assert
    assert [alasan.code for alasan in item.reasons] == [
        SESSION_NOT_ACCEPTED_AS_IS,
        SESSION_NOT_GRADED,
    ]


def test_the_order_given_by_the_reader_is_kept_and_nothing_is_dropped():
    # Arrange
    a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
    batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)
    c = _rekaman("C", [_nilai(1, "0.5", "0.5")])

    # Act
    hasil = _daftar(a, batal, c)

    # Assert
    assert [item.product_name for item in hasil] == ["A", "Batal", "C"]
    assert [item.eligible for item in hasil] == [True, False, True]


def test_no_sessions_gives_an_empty_list():
    # Act
    hasil = _daftar()

    # Assert
    assert hasil == []


@pytest.mark.parametrize(
    ("saring", "diharapkan"),
    [
        (MeasurementFilter.ALL, ["Otomatis", "Manual"]),
        (MeasurementFilter.AUTOMATIC, ["Otomatis"]),
        (MeasurementFilter.MANUAL, ["Manual"]),
    ],
)
def test_the_measurement_filter_keeps_only_that_kind(saring, diharapkan):
    # Arrange
    otomatis = _rekaman("Otomatis", [_nilai(1, "0.5", "0.5")])
    manual = _rekaman(
        "Manual",
        [_nilai(1, "0.5", "0.5")],
        session_type=SessionType.MANUAL,
        status=SessionStatus.FINALIZED,
    )

    # Act
    hasil = _daftar(otomatis, manual, measurement_filter=saring)

    # Assert
    assert [item.product_name for item in hasil] == diharapkan


def test_a_session_without_a_known_judge_model_has_none():
    # Arrange
    belum = _rekaman("Belum", [_nilai(1, "0.5", "0.5")], rubrik=None, judge_versi=None)

    # Act
    (item,) = _daftar(belum)

    # Assert
    assert item.judge_model_version is None
    assert item.product_id == belum.facts.product_id
