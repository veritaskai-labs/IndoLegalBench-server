"""Check that chosen suites can be measured. SCRUM-76 SCRUM-206.

The measurement summary takes several suite ids. A missing or deleted one
is a 404 for the whole request. An archived suite cannot be chosen for a new
measurement (suite AC4), so one archived id is a 409 for the whole request,
even when the other ids are live suites.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.suites import service
from app.modules.suites.models import Suite, SuiteStatus
from app.shared.exceptions import ConflictError, NotFoundError
from tests.modules.conftest import USER_ID_QA


def _suite(db, name: str, **ubah) -> uuid.UUID:
    suite = Suite(name=name, created_by=USER_ID_QA, **ubah)
    db.add(suite)
    db.commit()
    return suite.id


def test_semua_suite_ada_tidak_melempar_apa_pun(db_session):
    # Arrange
    a = _suite(db_session, "Suite A")
    b = _suite(db_session, "Suite B")

    # Act
    hasil = service.require_existing(db_session, {a, b})

    # Assert
    assert hasil is None


def test_salah_satu_suite_tidak_ada_dijawab_404(db_session):
    # Arrange
    ada = _suite(db_session, "Suite A")

    # Act
    with pytest.raises(NotFoundError) as galat:
        service.require_existing(db_session, {ada, uuid.uuid4()})

    # Assert
    assert galat.value.status_code == 404
    assert galat.value.message == "Suite tidak ditemukan"


def test_suite_yang_sudah_dihapus_dijawab_404(db_session):
    # Arrange
    dihapus = _suite(db_session, "Suite lama", deleted_at=datetime.now(UTC))

    # Act
    with pytest.raises(NotFoundError) as galat:
        service.require_existing(db_session, {dihapus})

    # Assert
    assert galat.value.status_code == 404
    assert galat.value.message == "Suite tidak ditemukan"


def test_suite_yang_diarsipkan_ditolak_409(db_session):
    # Arrange
    arsip = _suite(db_session, "Suite arsip", status=SuiteStatus.ARCHIVED)

    # Act
    with pytest.raises(ConflictError) as galat:
        service.require_existing(db_session, {arsip})

    # Assert
    assert galat.value.status_code == 409
    assert galat.value.code == service.SUITE_ARCHIVED
    assert galat.value.message == "Suite yang diarsipkan tidak dapat dipilih untuk pengukuran"


def test_suite_arsip_yang_dicampur_suite_aktif_tetap_ditolak_409(db_session):
    # Arrange
    aktif = _suite(db_session, "Suite aktif")
    arsip = _suite(db_session, "Suite arsip", status=SuiteStatus.ARCHIVED)

    # Act
    with pytest.raises(ConflictError) as galat:
        service.require_existing(db_session, {aktif, arsip})

    # Assert
    assert galat.value.status_code == 409
    assert galat.value.code == service.SUITE_ARCHIVED


def test_suite_yang_tidak_ada_tetap_404_walau_ada_suite_arsip(db_session):
    # Arrange
    arsip = _suite(db_session, "Suite arsip", status=SuiteStatus.ARCHIVED)

    # Act
    with pytest.raises(NotFoundError) as galat:
        service.require_existing(db_session, {arsip, uuid.uuid4()})

    # Assert
    assert galat.value.status_code == 404


def test_daftar_kosong_tidak_melempar_apa_pun(db_session):
    # Arrange: tidak ada suite

    # Act
    hasil = service.require_existing(db_session, set())

    # Assert
    assert hasil is None
