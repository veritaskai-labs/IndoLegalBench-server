"""Test schema pencarian dan tampilan audit log (PBI-18 AC3, AC4).

Waktu tanpa zona dianggap WIB, karena itu yang dilihat Admin di layar.
Semua waktu dinormalkan ke UTC sebelum masuk query.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.modules.audit.models import AuditEntityType
from app.modules.audit.schemas import AuditLogFilter, AuditLogRead

WIB = timezone(timedelta(hours=7))


def test_filter_kosong_boleh():
    # Act
    filter_ = AuditLogFilter()

    # Assert
    assert (filter_.occurred_from, filter_.occurred_to, filter_.actor) == (None, None, None)
    assert (filter_.page, filter_.size) == (1, 20)


def test_none_eksplisit_dari_router_tetap_kosong():
    """Router meneruskan query param yang tidak diisi sebagai None."""
    # Act
    filter_ = AuditLogFilter(occurred_from=None, occurred_to=None, actor=None)

    # Assert
    assert (filter_.occurred_from, filter_.occurred_to, filter_.actor) == (None, None, None)


def test_waktu_tanpa_zona_dianggap_wib():
    # Act
    filter_ = AuditLogFilter(occurred_from=datetime(2026, 10, 8, 7, 0))

    # Assert
    assert filter_.occurred_from == datetime(2026, 10, 8, 0, 0, tzinfo=UTC)


def test_waktu_dengan_zona_dinormalkan_ke_utc():
    # Act
    filter_ = AuditLogFilter(occurred_to=datetime(2026, 10, 8, 12, 0, tzinfo=WIB))

    # Assert
    assert filter_.occurred_to == datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
    assert filter_.occurred_to.tzinfo is UTC


def test_rentang_terbalik_ditolak():
    # Act
    with pytest.raises(ValidationError) as galat:
        AuditLogFilter(occurred_from=datetime(2026, 10, 9), occurred_to=datetime(2026, 10, 8))

    # Assert
    assert "occurred_from" in str(galat.value)


def test_rentang_satu_titik_boleh():
    # Arrange
    titik = datetime(2026, 10, 8, 9, 30)

    # Act
    filter_ = AuditLogFilter(occurred_from=titik, occurred_to=titik)

    # Assert
    assert filter_.occurred_from == filter_.occurred_to


@pytest.mark.parametrize("nama", ["", "   "])
def test_nama_pelaku_kosong_ditolak(nama):
    # Act + Assert
    with pytest.raises(ValidationError):
        AuditLogFilter(actor=nama)


def test_nama_pelaku_dirapikan():
    # Act + Assert
    assert AuditLogFilter(actor="  Rina ").actor == "Rina"


def test_jenis_data_di_luar_katalog_ditolak():
    # Act + Assert
    with pytest.raises(ValidationError):
        AuditLogFilter(entity_type="session")


@pytest.mark.parametrize(("page", "size"), [(0, 20), (1, 0), (1, 101)])
def test_paginasi_di_luar_batas_ditolak(page, size):
    # Act + Assert
    with pytest.raises(ValidationError):
        AuditLogFilter(page=page, size=size)


def test_offset_paginasi():
    # Act + Assert
    assert AuditLogFilter(page=3, size=20).offset == 40


def test_tampilan_menandai_waktu_sqlite_sebagai_utc():
    """SQLite mengembalikan waktu tanpa zona. Nilainya UTC, jadi ditandai UTC."""
    # Act
    baris = AuditLogRead(
        id=1,
        occurred_at=datetime(2026, 10, 8, 2, 0),
        actor_user_id=None,
        actor_name=None,
        actor_role=None,
        action="suite.created",
        entity_type=AuditEntityType.SUITE,
        entity_id=uuid.uuid4(),
        case_id=None,
        before=None,
        after={"name": "A"},
        reason=None,
        request_id=None,
    )

    # Assert
    assert baris.occurred_at == datetime(2026, 10, 8, 2, 0, tzinfo=UTC)
