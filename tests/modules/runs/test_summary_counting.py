"""Case count for a filter and total calls. SCRUM-76 SCRUM-206.

PBI-11 AC2 and AC3: the filter picks which split tags count, and the
total number of calls is cases times repeats.
"""

import pytest

from app.modules.runs import schemas, service

COUNTS = {"dev": 20, "test": 5}


@pytest.mark.parametrize(
    ("case_filter", "expected"),
    [("dev", 20), ("test", 5), ("both", 25)],
)
def test_jumlah_kasus_mengikuti_filter(case_filter, expected):
    # Arrange: 20 kasus dev dan 5 kasus test yang approved
    pilihan = schemas.CaseFilter(case_filter)

    # Act
    total = service.eligible_case_count(COUNTS, pilihan)

    # Assert
    assert total == expected


def test_split_yang_tidak_ada_di_hitungan_dianggap_nol():
    # Arrange: hanya dev yang punya kasus
    counts = {"dev": 7}

    # Act
    hanya_test = service.eligible_case_count(counts, schemas.CaseFilter.TEST)
    keduanya = service.eligible_case_count(counts, schemas.CaseFilter.BOTH)

    # Assert
    assert hanya_test == 0
    assert keduanya == 7


def test_hitungan_kosong_menghasilkan_nol():
    # Arrange + Act
    total = service.eligible_case_count({}, schemas.CaseFilter.BOTH)

    # Assert
    assert total == 0


@pytest.mark.parametrize(
    ("cases", "repeats", "expected"),
    [(20, 3, 60), (25, 1, 25), (100, 5, 500), (1, 1, 1)],
)
def test_total_panggilan_adalah_kasus_kali_pengulangan(cases, repeats, expected):
    # Arrange + Act
    total = service.total_calls(cases, repeats)

    # Assert
    assert total == expected


def test_total_panggilan_nol_saat_tidak_ada_kasus():
    # Arrange + Act
    total = service.total_calls(0, 5)

    # Assert
    assert total == 0
