"""Urutan daftar kasus lintas suite. PBI-4 SCRUM-81 SCRUM-379, AC3 (feedback UAT).

GET /cases menerima `sort` (kolom) dan `order` (asc/desc). Hanya kolom data yang bermakna yang
boleh: kode (kolom "ID" di layar), judul, status, suite, tag, dan waktu terakhir diubah. Kolom Aksi
dan kolom hitungan (completeness_pct) tidak. Tanpa parameter urutannya tetap yang lama: terakhir
diubah di atas. Urutan hanya mengatur susunan, tidak pernah membuang kasus.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = data batas (alasannya ditulis).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.cases.models import Case, CaseStatus, SplitTag
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _suite


@pytest.fixture
def penulis(as_role):
    return as_role(Role.CONTRIBUTOR)


def _atur(db_session, case_id: str, *, status, tag, judul, hari: int) -> None:
    """Set the in-effect version in one flush, so the explicit updated_at is kept."""
    versi = db_session.get(Case, uuid.UUID(case_id)).current_version
    versi.status = status
    versi.split_tag = tag
    versi.content = {**versi.content, "title": judul}
    versi.updated_at = datetime(2026, 11, hari, tzinfo=UTC)
    db_session.commit()


def _empat_kasus(client, db_session) -> dict[str, str]:
    """Four cases that differ on every sortable column, as {case_code: id}.

    code  title     status          suite    tag   updated
    K-1   bravo     in_review       Pidana   dev   3 Nov
    K-2   Alpha     approved        agraria  test  1 Nov
    K-3   charlie   draft           Pidana   -     2 Nov
    K-4   (none)    needs_revision  agraria  dev   4 Nov

    Suite names differ in letter case on purpose: "Pidana" sorts before "agraria" byte by byte.
    """
    pidana = _suite(client, "Pidana")
    agraria = _suite(client, "agraria")
    rencana = [
        ("K-1", pidana, "bravo", CaseStatus.IN_REVIEW, SplitTag.DEV, 3),
        ("K-2", agraria, "Alpha", CaseStatus.APPROVED, SplitTag.TEST, 1),
        ("K-3", pidana, "charlie", CaseStatus.DRAFT, None, 2),
        ("K-4", agraria, None, CaseStatus.NEEDS_REVISION, SplitTag.DEV, 4),
    ]
    ids = {}
    for kode, suite_id, judul, status, tag, hari in rencana:
        response = client.post(f"/suites/{suite_id}/cases", json=_badan(case_code=kode))
        assert response.status_code == 201
        ids[kode] = response.json()["id"]
        _atur(db_session, ids[kode], status=status, tag=tag, judul=judul, hari=hari)
    return ids


def _kode(client, **params) -> list[str]:
    body = client.get("/cases", params=params).json()
    return [item["case_code"] for item in body["items"]]


# --- Siklus 1: urutan per kolom ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sort", "order", "diharapkan"),
    [
        ("case_code", "asc", ["K-1", "K-2", "K-3", "K-4"]),
        ("case_code", "desc", ["K-4", "K-3", "K-2", "K-1"]),
        ("title", "asc", ["K-2", "K-1", "K-3", "K-4"]),
        ("title", "desc", ["K-3", "K-1", "K-2", "K-4"]),
        ("status", "asc", ["K-3", "K-1", "K-4", "K-2"]),
        ("status", "desc", ["K-2", "K-4", "K-1", "K-3"]),
        ("suite", "asc", ["K-2", "K-4", "K-1", "K-3"]),
        ("suite", "desc", ["K-1", "K-3", "K-2", "K-4"]),
        ("split_tag", "asc", ["K-1", "K-4", "K-2", "K-3"]),
        ("split_tag", "desc", ["K-2", "K-1", "K-4", "K-3"]),
        ("updated_at", "asc", ["K-2", "K-3", "K-1", "K-4"]),
        ("updated_at", "desc", ["K-4", "K-1", "K-3", "K-2"]),
    ],
)
def test_urutan_per_kolom_dan_arah(penulis, db_session, sort, order, diharapkan):
    """Positif: tiap kolom di whitelist bisa diurutkan naik dan turun. Judul tanpa isi dan tag
    kosong selalu di akhir (bukan di awal saat naik), nama suite dan judul tidak peka huruf besar,
    status mengikuti alur hidup (draft, in_review, needs_revision, approved) bukan abjad, dan
    kasus satu suite yang seri diurutkan menurut kode naik di kedua arah."""
    # Arrange
    _empat_kasus(penulis, db_session)

    # Act
    kode = _kode(penulis, sort=sort, order=order)

    # Assert
    assert kode == diharapkan


def test_tanpa_parameter_urutan_lama_tetap(penulis, db_session):
    """Positif: client lama yang belum mengirim sort tetap mendapat terakhir diubah di atas."""
    # Arrange
    _empat_kasus(penulis, db_session)

    # Act
    kode = _kode(penulis)

    # Assert
    assert kode == ["K-4", "K-1", "K-3", "K-2"]


@pytest.mark.parametrize(
    ("sort", "diharapkan"),
    [
        ("updated_at", ["K-4", "K-1", "K-3", "K-2"]),
        ("case_code", ["K-1", "K-2", "K-3", "K-4"]),
        ("title", ["K-2", "K-1", "K-3", "K-4"]),
    ],
)
def test_arah_bawaan_per_kolom(penulis, db_session, sort, diharapkan):
    """Positif: sort tanpa order memakai arah yang wajar untuk kolomnya: waktu dari yang terbaru,
    teks dari awal abjad."""
    # Arrange
    _empat_kasus(penulis, db_session)

    # Act
    kode = _kode(penulis, sort=sort)

    # Assert
    assert kode == diharapkan


def test_order_tanpa_sort_membalik_urutan_bawaan(penulis, db_session):
    """Edge: order saja berarti kolom bawaan (terakhir diubah) dengan arah itu."""
    # Arrange
    _empat_kasus(penulis, db_session)

    # Act
    kode = _kode(penulis, order="asc")

    # Assert
    assert kode == ["K-2", "K-3", "K-1", "K-4"]


# --- Siklus 2: tidak ada kasus yang hilang, bekerja bersama filter dan halaman ------------


@pytest.mark.parametrize(
    "sort", ["case_code", "title", "status", "suite", "split_tag", "updated_at"]
)
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_urutan_tidak_membuang_kasus(penulis, db_session, sort, order):
    """Edge: AC3, urutan hanya menyusun ulang. Judul kosong, tag kosong, dan suite berbeda tetap
    tampil, dan total sama dengan daftar tanpa urutan; join atau filter pada kolom urutan akan
    membuang baris yang nilainya NULL."""
    # Arrange
    ids = _empat_kasus(penulis, db_session)

    # Act
    body = penulis.get("/cases", params={"sort": sort, "order": order}).json()

    # Assert
    assert {item["id"] for item in body["items"]} == set(ids.values())
    assert len(body["items"]) == body["total"] == 4


def test_halaman_urutan_judul_stabil_dan_tidak_tumpang_tindih(penulis, db_session):
    """Edge: tiga kasus berstatus sama. Dengan size=1 ketiga halaman harus memberi tiga kasus
    berbeda menurut kode; tanpa urutan kedua yang pasti, kasus seri bisa muncul dua kali."""
    # Arrange
    suite_id = _suite(penulis)
    for kode in ("C-1", "A-1", "B-1"):
        assert (
            penulis.post(f"/suites/{suite_id}/cases", json=_badan(case_code=kode)).status_code
            == 201
        )

    # Act
    halaman = [_kode(penulis, sort="status", order="desc", page=n, size=1) for n in (1, 2, 3)]

    # Assert
    assert halaman == [["A-1"], ["B-1"], ["C-1"]]


def test_urutan_berjalan_bersama_kata_kunci_status_suite_dan_halaman(penulis, db_session):
    """Positif: urutan dipakai setelah semua filter, dan total hanya menghitung baris yang
    cocok filter."""
    # Arrange
    ids = _empat_kasus(penulis, db_session)
    suite_pidana = penulis.get("/cases", params={"q": "K-1"}).json()["items"][0]["suite_id"]

    # Act
    kata_kunci = _kode(penulis, q="k-", sort="case_code", order="desc", size=2)
    status = _kode(penulis, status="draft", sort="title", order="asc")
    suite = penulis.get(
        "/cases", params={"suite_id": suite_pidana, "sort": "updated_at", "order": "asc"}
    ).json()
    halaman_dua = penulis.get(
        "/cases", params={"sort": "case_code", "order": "asc", "page": 2, "size": 3}
    ).json()

    # Assert
    assert kata_kunci == ["K-4", "K-3"]
    assert status == ["K-3"]
    assert [item["id"] for item in suite["items"]] == [ids["K-3"], ids["K-1"]]
    assert suite["total"] == 2
    assert ([item["case_code"] for item in halaman_dua["items"]], halaman_dua["total"]) == (
        ["K-4"],
        4,
    )


def test_urutan_suite_tidak_menampilkan_kasus_suite_yang_dihapus(penulis, db_session):
    """Edge: kasus dari suite yang dihapus tetap tidak tampil (SCRUM-339 fix) meski urutan suite
    memakai peringkat nama suite; peringkat tidak boleh memunculkan atau memecahkan baris itu."""
    # Arrange
    ada = _suite(penulis, "Ada")
    hilang = _suite(penulis, "Hilang")
    for kode, suite_id in (("A-1", ada), ("H-1", hilang)):
        assert (
            penulis.post(f"/suites/{suite_id}/cases", json=_badan(case_code=kode)).status_code
            == 201
        )
    assert penulis.delete(f"/suites/{hilang}").status_code in (200, 204)

    # Act
    response = penulis.get("/cases", params={"sort": "suite", "order": "desc"})

    # Assert
    assert response.status_code == 200
    assert ([item["case_code"] for item in response.json()["items"]], response.json()["total"]) == (
        ["A-1"],
        1,
    )


# --- Siklus 3: input yang salah ditolak ----------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "aksi"},
        {"sort": "completeness_pct"},
        {"sort": ""},
        {"sort": "Title"},
        {"order": "sideways"},
        {"order": "ASC"},
        {"sort": "title", "order": ""},
    ],
    ids=[
        "aksi",
        "hitungan",
        "kosong",
        "huruf-besar",
        "arah-salah",
        "arah-huruf-besar",
        "arah-kosong",
    ],
)
def test_sort_atau_order_di_luar_whitelist_ditolak(penulis, params):
    """Negatif: kolom Aksi, kolom hitungan, dan arah yang tidak dikenal ditolak 422, bukan
    diam-diam diabaikan, supaya salah ketik di client terlihat dan bukan jadi urutan bawaan."""
    # Act
    response = penulis.get("/cases", params=params)

    # Assert
    assert response.status_code == 422


def test_sort_ganda_ditolak_atau_hanya_satu_dipakai(penulis, db_session):
    """Edge: ?sort=title&sort=status tidak boleh membuat 500; FastAPI memakai nilai terakhir."""
    # Arrange
    _empat_kasus(penulis, db_session)

    # Act
    response = penulis.get("/cases?sort=title&sort=case_code&order=asc")

    # Assert
    assert response.status_code == 200
    assert [item["case_code"] for item in response.json()["items"]] == ["K-1", "K-2", "K-3", "K-4"]


def test_developer_ai_tetap_ditolak_dengan_parameter_urutan(as_role):
    """Negatif: parameter urutan tidak membuka akses; daftar lintas suite hanya untuk Kontributor
    dan Admin."""
    # Arrange
    developer = as_role(Role.DEVELOPER_AI)

    # Act
    response = developer.get("/cases", params={"sort": "title", "order": "asc"})

    # Assert
    assert response.status_code == 403
