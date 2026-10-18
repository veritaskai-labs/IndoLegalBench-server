"""Hanya test case yang masuk skor resmi. PBI-9 AC4, SCRUM-164.

official_cases() adalah fondasi untuk penilaian dan laporan (Sprint 3):
versi yang berlaku, sudah disetujui, dan bertag test. Dev case dan kasus
yang belum disetujui tidak pernah ikut.
"""

import uuid

from app.modules.cases import service
from app.modules.cases.models import Case
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _setujui, _suite


def _kode(hasil) -> list[str]:
    return [salinan.case_code for salinan in hasil]


def _kasus(client, db_session, suite_id, kode, tag, *, setujui=True) -> str:
    case_id = _buat(client, suite_id, case_code=kode, split_tag=tag).json()["id"]
    if setujui:
        _setujui(db_session, case_id)
    return case_id


def test_hanya_test_case_yang_disetujui(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    _kasus(client, db_session, suite_id, "UJI-002", "test")
    _kasus(client, db_session, suite_id, "UJI-001", "test")
    _kasus(client, db_session, suite_id, "DEV-001", "dev")
    _kasus(client, db_session, suite_id, "DRAF-001", "test", setujui=False)

    # Act
    hasil = service.official_cases(db_session, uuid.UUID(suite_id))

    # Assert
    assert _kode(hasil) == ["UJI-001", "UJI-002"]
    assert {str(salinan.split_tag) for salinan in hasil} == {"test"}


def test_memakai_versi_yang_berlaku_bukan_draf_berikutnya(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    case_id = _kasus(client, db_session, suite_id, "UJI-001", "test")
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    versi_berlaku = db_session.get(Case, uuid.UUID(case_id)).latest_approved_version_id

    # Act
    [salinan] = service.official_cases(db_session, uuid.UUID(suite_id))

    # Assert
    assert salinan.case_version_id == versi_berlaku
    assert salinan.version_no == 1


def test_suite_lain_tidak_ikut(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_a = _suite(client, name="Suite A")
    suite_b = _suite(client, name="Suite B")
    _kasus(client, db_session, suite_a, "UJI-A", "test")
    _kasus(client, db_session, suite_b, "UJI-B", "test")

    # Act + Assert
    assert _kode(service.official_cases(db_session, uuid.UUID(suite_a))) == ["UJI-A"]


def test_suite_tanpa_test_case_kosong(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    _kasus(client, db_session, suite_id, "DEV-001", "dev")

    # Act + Assert
    assert service.official_cases(db_session, uuid.UUID(suite_id)) == []
