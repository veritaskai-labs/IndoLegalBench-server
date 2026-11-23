"""Daftar sesi yang layak jadi dasar laporan. PBI-15 SCRUM-79 SCRUM-313.

Murni, tanpa database: pembaca sesi diganti pembaca palsu. Yang diuji: tiap sesi diberi
eligible beserta alasannya, urutan dari pembaca dipertahankan, dan filter jenis pengukuran.
"""

import uuid

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_GRADED,
)
from app.modules.reports.eligible_sessions import (
    EligibleSessionLister,
    EligibleSessionQuery,
    SessionValidity,
)
from app.modules.reports.models import MeasurementFilter
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from tests.modules.reports.test_report_generation import (
    AWAL,
    PembacaPalsu,
    _nilai,
    _ref,
    _rekaman,
)


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


@pytest.mark.parametrize(
    ("kasus", "diharapkan"),
    [
        ([SplitTag.TEST], (SplitTag.TEST,)),
        ([SplitTag.DEV], (SplitTag.DEV,)),
        ([SplitTag.TEST, SplitTag.DEV, SplitTag.TEST], (SplitTag.DEV, SplitTag.TEST)),
        ([], ()),
    ],
)
def test_a_session_lists_the_case_kinds_in_its_snapshot(kasus, diharapkan):
    # Arrange
    rekaman = _rekaman(
        "Produk A", [_nilai(nomor, "0.5", "0.5", split) for nomor, split in enumerate(kasus, 1)]
    )

    # Act
    (item,) = _daftar(rekaman)

    # Assert
    assert item.case_kinds == diharapkan


def test_case_kinds_come_from_the_snapshot_and_not_only_from_graded_cases():
    # Arrange: kasus dev ada di snapshot tetapi belum dinilai.
    dev_belum_dinilai = _ref(2, split=SplitTag.DEV)
    rekaman = _rekaman(
        "Produk A", [_nilai(1, "0.5", "0.5")], snapshot_tambahan=(dev_belum_dinilai,)
    )

    # Act
    (item,) = _daftar(rekaman)

    # Assert
    assert item.case_kinds == (SplitTag.DEV, SplitTag.TEST)


@pytest.fixture
def layak_dan_tidak():
    layak = _rekaman("Layak", [_nilai(1, "0.5", "0.5")])
    batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)
    belum = _rekaman("Belum", [_nilai(1, "0.5", "0.5")], rubrik=None)
    return layak, batal, belum


@pytest.mark.parametrize(
    ("validitas", "diharapkan"),
    [
        (SessionValidity.ALL, ["Layak", "Batal", "Belum"]),
        (SessionValidity.VALID, ["Layak"]),
        (SessionValidity.INVALID, ["Batal", "Belum"]),
    ],
)
def test_the_validity_filter_splits_eligible_from_ineligible(
    layak_dan_tidak, validitas, diharapkan
):
    # Act
    hasil = _daftar(*layak_dan_tidak, query=EligibleSessionQuery(validity=validitas))

    # Assert
    assert [item.product_name for item in hasil] == diharapkan


def test_the_validity_filter_keeps_the_reasons_of_invalid_sessions(layak_dan_tidak):
    # Act
    hasil = _daftar(*layak_dan_tidak, query=EligibleSessionQuery(validity=SessionValidity.INVALID))

    # Assert
    assert [[alasan.code for alasan in item.reasons] for item in hasil] == [
        [SESSION_CANCELLED],
        [SESSION_NOT_GRADED],
    ]


def test_no_query_means_no_filtering(layak_dan_tidak):
    # Act
    hasil = _daftar(*layak_dan_tidak)

    # Assert
    assert len(hasil) == 3


@pytest.fixture
def dua_produk():
    produk_a, produk_b = uuid.uuid4(), uuid.uuid4()
    a1 = _rekaman("A", [_nilai(1, "0.5", "0.5")], product_id=produk_a, label="A pertama")
    a2 = _rekaman("A", [_nilai(1, "0.5", "0.5")], product_id=produk_a, label="A kedua")
    b = _rekaman("B", [_nilai(1, "0.5", "0.5")], product_id=produk_b)
    return produk_a, (a1, a2, b)


def test_the_product_filter_keeps_every_session_of_that_product(dua_produk):
    # Arrange
    produk_a, rekaman = dua_produk

    # Act
    hasil = _daftar(*rekaman, query=EligibleSessionQuery(product_id=produk_a))

    # Assert
    assert [item.label for item in hasil] == ["A pertama", "A kedua"]
    assert {item.product_id for item in hasil} == {produk_a}


def test_the_product_filter_for_a_product_without_sessions_is_empty(dua_produk):
    # Arrange
    _, rekaman = dua_produk

    # Act
    hasil = _daftar(*rekaman, query=EligibleSessionQuery(product_id=uuid.uuid4()))

    # Assert
    assert hasil == []


def test_the_product_and_validity_filters_combine_with_and():
    # Arrange
    produk = uuid.uuid4()
    layak = _rekaman("A", [_nilai(1, "0.5", "0.5")], product_id=produk)
    batal = _rekaman(
        "A", [_nilai(1, "0.5", "0.5")], product_id=produk, status=SessionStatus.CANCELLED
    )
    lain_batal = _rekaman("B", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)

    # Act
    hasil = _daftar(
        layak,
        batal,
        lain_batal,
        query=EligibleSessionQuery(product_id=produk, validity=SessionValidity.INVALID),
    )

    # Assert
    assert [item.session_id for item in hasil] == [batal.facts.session_id]


@pytest.fixture
def tiga_sesi():
    alfa = _rekaman("AiYU", [_nilai(1, "0.5", "0.5")], label="Pengukuran Resmi Oktober")
    beta = _rekaman("Justisio", [_nilai(1, "0.5", "0.5")], label="Uji coba AiYU lama")
    gamma = _rekaman("LegalHero", [_nilai(1, "0.5", "0.5")], label="Pengukuran 100%_final")
    return alfa, beta, gamma


@pytest.mark.parametrize(
    ("kata", "diharapkan"),
    [
        ("oktober", ["AiYU"]),
        ("JUSTISIO", ["Justisio"]),
        ("  legalhero  ", ["LegalHero"]),
        ("aiyu", ["AiYU", "Justisio"]),
        ("100%_final", ["LegalHero"]),
        ("tidak-ada", []),
    ],
)
def test_the_search_matches_label_or_product_name_ignoring_case_and_padding(
    tiga_sesi, kata, diharapkan
):
    # Act
    hasil = _daftar(*tiga_sesi, query=EligibleSessionQuery(q=kata))

    # Assert
    assert [item.product_name for item in hasil] == diharapkan


def test_the_search_matches_a_part_of_the_session_id(tiga_sesi):
    # Arrange
    alfa, beta, _ = tiga_sesi
    potongan = str(beta.facts.session_id)[:13].upper()

    # Act
    hasil = _daftar(*tiga_sesi, query=EligibleSessionQuery(q=potongan))

    # Assert
    assert [item.session_id for item in hasil] == [beta.facts.session_id]


@pytest.mark.parametrize("kosong", [None, "", "   "])
def test_an_empty_search_does_not_filter(tiga_sesi, kosong):
    # Act
    hasil = _daftar(*tiga_sesi, query=EligibleSessionQuery(q=kosong))

    # Assert
    assert len(hasil) == 3


def test_the_search_combines_with_the_other_filters(tiga_sesi):
    # Arrange
    alfa, beta, gamma = tiga_sesi
    batal = _rekaman(
        "AiYU", [_nilai(1, "0.5", "0.5")], label="AiYU batal", status=SessionStatus.CANCELLED
    )

    # Act
    hasil = _daftar(
        alfa,
        beta,
        gamma,
        batal,
        query=EligibleSessionQuery(q="aiyu", validity=SessionValidity.VALID),
    )

    # Assert
    assert [item.session_id for item in hasil] == [alfa.facts.session_id, beta.facts.session_id]
