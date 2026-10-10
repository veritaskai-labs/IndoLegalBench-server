"""Unit test pencarian, detail, dan data ekspor audit log, dependency di-mock.

PBI-18 AC3 (Admin mencari dan menyaring), AC4 (catatan dibuka kembali),
AC5 (Reviewer hanya kasus yang ditugaskan). Repository dan auth.service
dipalsukan, jadi yang diuji hanya aturan di audit.service.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.audit import service
from app.modules.audit.repository import SearchCriteria
from app.modules.audit.schemas import AuditLogFilter
from app.shared.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.shared.security import Role

ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REVIEWER = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
RINA = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def _row(**kolom) -> SimpleNamespace:
    nilai = {
        "id": 1,
        "occurred_at": datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        "actor_user_id": RINA,
        "actor_role": "author",
        "action": "case.updated",
        "entity_type": "case",
        "entity_id": KASUS,
        "case_id": KASUS,
        "before": {"title": "A"},
        "after": {"title": "B"},
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


def _cari(filter_=None, *, role=Role.ADMIN, viewer=ADMIN):
    return service.search_logs(
        MagicMock(), filter_ or AuditLogFilter(), viewer_id=viewer, viewer_role=role
    )


# --- AC3: Admin ---------------------------------------------------------------


def test_admin_melihat_semua_tanpa_cakupan(repo, auth):
    # Act
    halaman = _cari()

    # Assert
    kriteria = repo.search.call_args.args[1]
    assert kriteria == SearchCriteria()
    assert (halaman.total, halaman.page, halaman.size) == (1, 1, 20)


def test_hasil_membawa_nama_pelaku(repo, auth):
    # Act
    halaman = _cari()

    # Assert
    auth.user_names.assert_called_once()
    assert halaman.items[0].actor_name == "Rina Sari"
    assert halaman.items[0].after == {"title": "B"}


def test_pelaku_sistem_tanpa_nama(repo, auth):
    # Arrange
    repo.search.return_value = [_row(actor_user_id=None, actor_role=None)]

    # Act
    halaman = _cari()

    # Assert
    assert halaman.items[0].actor_name is None
    assert None not in auth.user_names.call_args.args[1]


def test_saringan_diteruskan_ke_repository(repo, auth):
    # Arrange
    filter_ = AuditLogFilter(
        occurred_from=datetime(2026, 10, 1, tzinfo=UTC),
        occurred_to=datetime(2026, 10, 8, tzinfo=UTC),
        entity_type="suite",
        actor_id=RINA,
        page=3,
        size=10,
    )

    # Act
    _cari(filter_)

    # Assert
    kriteria = repo.search.call_args.args[1]
    assert kriteria.occurred_from == datetime(2026, 10, 1, tzinfo=UTC)
    assert kriteria.entity_type == "suite"
    assert kriteria.actor_ids == (RINA,)
    assert repo.search.call_args.kwargs == {"offset": 20, "limit": 10}


def test_saring_nama_pengguna_lewat_auth(repo, auth):
    """AC3: saring berdasarkan nama pengguna tertentu."""
    # Act
    _cari(AuditLogFilter(actor="rina"))

    # Assert
    auth.find_user_ids.assert_called_once_with(auth.find_user_ids.call_args.args[0], "rina")
    assert repo.search.call_args.args[1].actor_ids == (RINA,)


def test_nama_dan_id_pelaku_digabung(repo, auth):
    # Arrange
    auth.find_user_ids.return_value = [RINA, ADMIN]

    # Act
    _cari(AuditLogFilter(actor="a", actor_id=ADMIN))

    # Assert
    assert repo.search.call_args.args[1].actor_ids == (ADMIN,)


def test_nama_tidak_dikenal_langsung_kosong_tanpa_query(repo, auth):
    # Arrange
    auth.find_user_ids.return_value = []

    # Act
    halaman = _cari(AuditLogFilter(actor="tidak-ada"))

    # Assert
    assert (halaman.items, halaman.total) == ([], 0)
    repo.search.assert_not_called()


# --- AC5: Reviewer ------------------------------------------------------------


def test_reviewer_otomatis_dibatasi_kasus_yang_ditugaskan(repo, auth):
    # Arrange
    with patch.object(service, "_assigned_case_ids", return_value=frozenset({KASUS})):
        # Act
        _cari(role=Role.REVIEWER, viewer=REVIEWER)

    # Assert
    assert repo.search.call_args.args[1].case_scope == frozenset({KASUS})


def test_reviewer_tanpa_tugas_tidak_melihat_apa_pun(repo, auth):
    """Sampai tabel review PBI-6 ada, Reviewer belum punya kasus yang ditugaskan."""
    # Act
    _cari(role=Role.REVIEWER, viewer=REVIEWER)

    # Assert
    assert repo.search.call_args.args[1].case_scope == frozenset()


def test_reviewer_minta_kasus_di_luar_tugas_ditolak(repo, auth):
    # Act
    with pytest.raises(ForbiddenError):
        _cari(AuditLogFilter(case_id=KASUS), role=Role.REVIEWER, viewer=REVIEWER)

    # Assert
    repo.search.assert_not_called()


def test_reviewer_minta_kasus_yang_ditugaskan_boleh(repo, auth):
    # Arrange
    with patch.object(service, "_assigned_case_ids", return_value=frozenset({KASUS})):
        # Act
        _cari(AuditLogFilter(case_id=KASUS), role=Role.REVIEWER, viewer=REVIEWER)

    # Assert
    assert repo.search.call_args.args[1].case_id == KASUS


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.VIEWER])
def test_peran_lain_ditolak(repo, auth, role):
    # Act
    with pytest.raises(ForbiddenError):
        _cari(role=role)

    # Assert
    repo.search.assert_not_called()


# --- AC4: detail --------------------------------------------------------------


def test_admin_membuka_satu_catatan(repo, auth):
    # Act
    catatan = service.get_log(MagicMock(), 1, viewer_id=ADMIN, viewer_role=Role.ADMIN)

    # Assert
    assert (catatan.id, catatan.actor_name, catatan.action) == (1, "Rina Sari", "case.updated")


def test_catatan_tidak_ada(repo, auth):
    # Arrange
    repo.get_by_id.return_value = None

    # Act + Assert
    with pytest.raises(NotFoundError):
        service.get_log(MagicMock(), 99, viewer_id=ADMIN, viewer_role=Role.ADMIN)


def test_reviewer_membuka_catatan_di_luar_tugas_ditolak(repo, auth):
    # Act + Assert
    with pytest.raises(ForbiddenError):
        service.get_log(MagicMock(), 1, viewer_id=REVIEWER, viewer_role=Role.REVIEWER)


def test_reviewer_membuka_catatan_kasus_yang_ditugaskan(repo, auth):
    # Arrange
    with patch.object(service, "_assigned_case_ids", return_value=frozenset({KASUS})):
        # Act
        catatan = service.get_log(MagicMock(), 1, viewer_id=REVIEWER, viewer_role=Role.REVIEWER)

    # Assert
    assert catatan.case_id == KASUS


# --- AC6: data ekspor ---------------------------------------------------------


def test_ekspor_mengambil_semua_baris_tanpa_paginasi(repo, auth):
    # Act
    baris = service.list_for_export(
        MagicMock(), AuditLogFilter(page=5, size=10), viewer_id=ADMIN, viewer_role=Role.ADMIN
    )

    # Assert
    assert [row.id for row in baris] == [1]
    assert repo.search.call_args.kwargs == {"offset": 0, "limit": service.EXPORT_LIMIT}


def test_ekspor_terlalu_besar_diminta_mempersempit(repo, auth):
    # Arrange
    repo.count.return_value = service.EXPORT_LIMIT + 1

    # Act
    with pytest.raises(ValidationError) as galat:
        service.list_for_export(
            MagicMock(), AuditLogFilter(), viewer_id=ADMIN, viewer_role=Role.ADMIN
        )

    # Assert
    assert galat.value.code == "AUDIT_EXPORT_TOO_LARGE"
    repo.search.assert_not_called()


def test_ekspor_reviewer_ikut_cakupan(repo, auth):
    # Act
    service.list_for_export(
        MagicMock(), AuditLogFilter(), viewer_id=REVIEWER, viewer_role=Role.REVIEWER
    )

    # Assert
    assert repo.search.call_args.args[1].case_scope == frozenset()


def test_ekspor_nama_tidak_dikenal_menghasilkan_file_kosong(repo, auth):
    # Arrange
    auth.find_user_ids.return_value = []

    # Act
    baris = service.list_for_export(
        MagicMock(), AuditLogFilter(actor="tidak-ada"), viewer_id=ADMIN, viewer_role=Role.ADMIN
    )

    # Assert
    assert baris == []
    repo.count.assert_not_called()
