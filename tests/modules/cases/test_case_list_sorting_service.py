"""Service daftar kasus meneruskan urutan ke repository. PBI-4 SCRUM-81 SCRUM-379, AC3.

Repository, katalog suite, dan katalog rujukan hukum di-mock: yang diuji hanya keputusan service
(arah bawaan per kolom dan peringkat suite), tanpa database.

Label: Positif = jalur normal, Edge = data batas (alasannya ditulis).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases import service


@pytest.fixture
def repo():
    with (
        patch.object(service.repository, "page_in_effect", return_value=([], 0)) as page,
        patch.object(service.legal_refs_service, "load_catalog", return_value=MagicMock()),
        patch("app.modules.suites.service.search_catalog") as katalog,
    ):
        katalog.return_value = []
        page.katalog = katalog
        yield page


@pytest.mark.parametrize(
    ("sort", "order", "arah"),
    [
        ("updated_at", None, "desc"),
        ("case_code", None, "asc"),
        ("title", None, "asc"),
        ("status", None, "asc"),
        ("updated_at", "asc", "asc"),
        ("title", "desc", "desc"),
    ],
)
def test_arah_bawaan_dan_arah_dari_pengguna(repo, sort, order, arah):
    """Positif: order kosong memakai arah bawaan kolom, order dari pengguna menang."""
    # Arrange
    db = MagicMock()

    # Act
    service.list_all_cases(db, sort=sort, order=order)

    # Assert
    kwargs = repo.call_args.kwargs
    assert (kwargs["sort"], kwargs["order"]) == (sort, arah)


def test_tanpa_sort_memakai_terakhir_diubah_turun(repo):
    """Positif: pemanggil lama (tanpa sort) tetap mendapat urutan lama."""
    # Act
    service.list_all_cases(MagicMock())

    # Assert
    kwargs = repo.call_args.kwargs
    assert (kwargs["sort"], kwargs["order"]) == ("updated_at", "desc")


def test_urutan_suite_memberi_peringkat_nama_tanpa_peka_huruf_besar(repo):
    """Positif: peringkat dihitung dari nama suite tanpa membedakan huruf besar kecil, supaya
    "agraria" mendahului "Pidana"; repository tidak perlu tahu tentang modul suites."""
    # Arrange
    pidana, agraria = uuid.uuid4(), uuid.uuid4()
    repo.katalog.return_value = [(pidana, "Pidana", False), (agraria, "agraria", True)]

    # Act
    service.list_all_cases(MagicMock(), sort="suite", order="asc")

    # Assert
    assert repo.call_args.kwargs["suite_rank"] == {agraria: 0, pidana: 1}


def test_nama_suite_kembar_beda_huruf_diberi_peringkat_berbeda(repo):
    """Edge: dua suite yang namanya sama selain huruf besar tetap mendapat peringkat berbeda
    (urut menurut id), jadi peringkat tidak pernah menggabungkan dua suite."""
    # Arrange
    kecil, besar = uuid.UUID(int=1), uuid.UUID(int=2)
    repo.katalog.return_value = [(besar, "Pidana", False), (kecil, "pidana", False)]

    # Act
    service.list_all_cases(MagicMock(), sort="suite")

    # Assert
    peringkat = repo.call_args.kwargs["suite_rank"]
    assert sorted(peringkat.values()) == [0, 1]
    assert peringkat[kecil] < peringkat[besar]


def test_kolom_lain_tidak_membuat_peringkat_suite(repo):
    """Positif: peringkat hanya dihitung bila diurutkan menurut suite."""
    # Act
    service.list_all_cases(MagicMock(), sort="title")

    # Assert
    assert repo.call_args.kwargs["suite_rank"] is None
