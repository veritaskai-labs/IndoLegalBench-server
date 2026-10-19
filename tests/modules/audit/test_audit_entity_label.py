"""Unit test label objek dan saringan kode kasus audit log (F-06, TC-31).

PBI-18 AC3 dan AC4. Pembaca di luar tim tidak bisa membaca UUID, jadi
setiap catatan membawa entity_label: kode kasus untuk catatan kasus dan
review, nama suite, nama produk AI, atau nama pengguna. Admin juga bisa
mencari lewat kode kasus.

Repository dan service modul lain dipalsukan (patch.object), jadi yang
diuji hanya aturan di audit.service.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.audit import service
from app.modules.audit.schemas import AuditLogFilter
from app.shared.security import Role

ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REVIEWER = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
RINA = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
KASUS_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
SUITE = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
PRODUK = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
PENUGASAN = uuid.UUID("00000000-0000-0000-0000-0000000000f2")


def _row(**kolom) -> SimpleNamespace:
    nilai = {
        "id": 1,
        "occurred_at": datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        "actor_user_id": RINA,
        "actor_role": "author",
        "action": "case.updated",
        "entity_type": "case",
        "entity_id": VERSI,
        "case_id": KASUS,
        "before": None,
        "after": None,
        "reason": None,
        "request_id": None,
    }
    nilai.update(kolom)
    return SimpleNamespace(**nilai)


@pytest.fixture
def repo():
    with patch.object(service, "repository") as palsu:
        palsu.search.return_value = [_row()]
        palsu.count.return_value = 1
        palsu.get_by_id.return_value = _row()
        yield palsu


@pytest.fixture
def auth():
    with patch.object(service, "auth_service") as palsu:
        palsu.user_names.return_value = {RINA: "Rina Sari"}
        palsu.find_user_ids.return_value = [RINA]
        yield palsu


@pytest.fixture
def review():
    with patch.object(service, "review_service") as palsu:
        palsu.assigned_case_ids.return_value = frozenset({KASUS})
        palsu.hidden_verdict_assignments.return_value = set()
        yield palsu


@pytest.fixture
def cases():
    with patch.object(service, "cases_service") as palsu:
        palsu.case_codes.return_value = {KASUS: "UAT-PHK-001"}
        palsu.find_case_ids.return_value = [KASUS]
        yield palsu


@pytest.fixture
def suites():
    with patch.object(service, "suites_service") as palsu:
        palsu.suite_names.return_value = {SUITE: "Ketenagakerjaan 2026"}
        yield palsu


@pytest.fixture
def providers():
    with patch.object(service, "providers_service") as palsu:
        palsu.product_names.return_value = {PRODUK: "AiYU"}
        yield palsu


@pytest.fixture
def modul(repo, auth, review, cases, suites, providers):
    return SimpleNamespace(
        repo=repo, auth=auth, review=review, cases=cases, suites=suites, providers=providers
    )


def _cari(filter_=None, *, role=Role.ADMIN, viewer=ADMIN):
    return service.search_logs(
        MagicMock(), filter_ or AuditLogFilter(), viewer_id=viewer, viewer_role=role
    )


def _label(modul, row) -> str | None:
    modul.repo.search.return_value = [row]
    return _cari().items[0].entity_label


# --- entity_label: positif ----------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        _row(entity_type="case", entity_id=KASUS),
        _row(entity_type="case", entity_id=VERSI, action="case.version_created"),
        _row(entity_type="case_version", entity_id=VERSI),
        _row(entity_type="review", entity_id=PENUGASAN, action="review.reviewer_assigned"),
    ],
    ids=["kasus", "versi-dicatat-sebagai-kasus", "case_version", "review"],
)
def test_catatan_kasus_dan_review_diberi_kode_kasus(modul, row):
    # Act
    label = _label(modul, row)

    # Assert
    assert label == "UAT-PHK-001"
    modul.cases.case_codes.assert_called_once()
    assert modul.cases.case_codes.call_args.args[1] == {KASUS}


def test_catatan_suite_diberi_nama_suite(modul):
    # Act
    label = _label(modul, _row(entity_type="suite", entity_id=SUITE, case_id=None))

    # Assert
    assert label == "Ketenagakerjaan 2026"
    assert modul.suites.suite_names.call_args.args[1] == {SUITE}


def test_catatan_produk_ai_diberi_nama_produk(modul):
    # Act
    label = _label(modul, _row(entity_type="ai_product", entity_id=PRODUK, case_id=None))

    # Assert
    assert label == "AiYU"
    assert modul.providers.product_names.call_args.args[1] == {PRODUK}


def test_catatan_pengguna_diberi_nama_pengguna_dalam_satu_query(modul):
    # Arrange
    budi = uuid.uuid4()
    modul.auth.user_names.return_value = {RINA: "Rina Sari", budi: "Budi"}

    # Act
    label = _label(modul, _row(entity_type="user", entity_id=budi, case_id=None))

    # Assert
    assert label == "Budi"
    # Nama pelaku dan nama objek pengguna diambil sekaligus.
    modul.auth.user_names.assert_called_once()
    assert modul.auth.user_names.call_args.args[1] == {RINA, budi}


def test_satu_query_per_jenis_untuk_banyak_baris(modul):
    # Arrange
    modul.repo.search.return_value = [
        _row(id=1, case_id=KASUS),
        _row(id=2, case_id=KASUS_LAIN),
        _row(id=3, entity_type="suite", entity_id=SUITE, case_id=None),
    ]
    modul.cases.case_codes.return_value = {KASUS: "UAT-PHK-001", KASUS_LAIN: "UAT-PHK-002"}

    # Act
    items = _cari().items

    # Assert
    assert [item.entity_label for item in items] == [
        "UAT-PHK-001",
        "UAT-PHK-002",
        "Ketenagakerjaan 2026",
    ]
    modul.cases.case_codes.assert_called_once()
    assert modul.cases.case_codes.call_args.args[1] == {KASUS, KASUS_LAIN}


def test_detail_dan_ekspor_juga_membawa_label(modul):
    # Act
    detail = service.get_log(MagicMock(), 1, viewer_id=ADMIN, viewer_role=Role.ADMIN)
    ekspor = service.list_for_export(
        MagicMock(), AuditLogFilter(), viewer_id=ADMIN, viewer_role=Role.ADMIN
    )

    # Assert
    assert detail.entity_label == "UAT-PHK-001"
    assert ekspor[0].entity_label == "UAT-PHK-001"


# --- entity_label: negatif dan edge -------------------------------------------


def test_kasus_yang_sudah_tidak_ada_tanpa_label(modul):
    # Arrange: baris kasus sudah hilang, catatannya tetap ada.
    modul.cases.case_codes.return_value = {}

    # Act + Assert
    assert _label(modul, _row()) is None


def test_catatan_kasus_tanpa_case_id_tanpa_label(modul):
    # Act
    label = _label(modul, _row(case_id=None))

    # Assert
    assert label is None
    assert modul.cases.case_codes.call_args.args[1] == set()


def test_jenis_objek_tak_dikenal_tanpa_label(modul):
    # Act + Assert
    assert _label(modul, _row(entity_type="measurement", case_id=None)) is None


def test_suite_dan_produk_yang_tidak_ada_tanpa_label(modul):
    # Arrange
    modul.suites.suite_names.return_value = {}
    modul.providers.product_names.return_value = {}

    # Act + Assert
    assert _label(modul, _row(entity_type="suite", entity_id=SUITE, case_id=None)) is None
    assert _label(modul, _row(entity_type="ai_product", entity_id=PRODUK, case_id=None)) is None


def test_halaman_kosong_tetap_tanpa_error(modul):
    # Arrange
    modul.repo.search.return_value = []
    modul.repo.count.return_value = 0

    # Act + Assert
    assert _cari().items == []


# --- saringan case_code -------------------------------------------------------


def test_kode_kasus_diterjemahkan_ke_id_kasus(modul):
    # Act
    _cari(AuditLogFilter(case_code="phk-001"))

    # Assert
    modul.cases.find_case_ids.assert_called_once()
    assert modul.cases.find_case_ids.call_args.args[1] == "phk-001"
    assert modul.repo.search.call_args.args[1].case_ids == (KASUS,)


def test_tanpa_kode_kasus_tidak_mencari_kasus(modul):
    # Act
    _cari()

    # Assert
    modul.cases.find_case_ids.assert_not_called()
    assert modul.repo.search.call_args.args[1].case_ids is None


def test_kode_kasus_tak_dikenal_hasil_kosong(modul):
    # Arrange
    modul.cases.find_case_ids.return_value = []

    # Act
    halaman = _cari(AuditLogFilter(case_code="TIDAK-ADA"))

    # Assert
    assert (halaman.items, halaman.total) == ([], 0)
    modul.repo.search.assert_not_called()


def test_kode_kasus_tak_dikenal_ekspor_kosong(modul):
    # Arrange
    modul.cases.find_case_ids.return_value = []

    # Act
    hasil = service.list_for_export(
        MagicMock(),
        AuditLogFilter(case_code="TIDAK-ADA"),
        viewer_id=ADMIN,
        viewer_role=Role.ADMIN,
    )

    # Assert
    assert hasil == []
    modul.repo.search.assert_not_called()


def test_reviewer_mencari_kode_kasus_tetap_dalam_cakupan(modul):
    # Arrange: kode cocok dengan dua kasus, hanya satu yang ditugaskan.
    modul.cases.find_case_ids.return_value = [KASUS, KASUS_LAIN]

    # Act
    _cari(AuditLogFilter(case_code="PHK"), role=Role.REVIEWER, viewer=REVIEWER)

    # Assert
    kriteria = modul.repo.search.call_args.args[1]
    assert kriteria.case_ids == (KASUS, KASUS_LAIN)
    assert kriteria.case_scope == frozenset({KASUS})
