"""Pintu cases.service untuk modul reviews. PBI-6, SCRUM-145.

reviews tidak membaca tabel cases sendiri. Isi versi untuk reviewer,
penulis kasus, dan hasil keputusan round lewat fungsi di sini.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases import service
from app.modules.cases.models import Case, CaseStatus
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _suite
from tests.modules.conftest import USER_ID_QA


def _diajukan(client) -> str:
    case_id = _buat(client, _suite(client)).json()["id"]
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    return case_id


def _kasus(db_session, case_id: str) -> Case:
    db_session.expire_all()
    return db_session.get(Case, uuid.UUID(case_id))


# --- Isi versi untuk reviewer -----------------------------------------------------


def test_salinan_review_memuat_isi_tag_dan_penulis(as_role, db_session):
    # Arrange
    case_id = _diajukan(as_role(Role.AUTHOR))
    versi = _kasus(db_session, case_id).current_version

    # Act
    salinan = service.review_copies(db_session, {versi.id})[versi.id]

    # Assert
    assert salinan.case_id == uuid.UUID(case_id)
    assert salinan.case_code == "PHK-001"
    assert salinan.author_id == USER_ID_QA
    assert salinan.version_no == 1
    assert salinan.split_tag == "dev"
    assert salinan.identity.title == "PHK sepihak"
    assert salinan.legal_refs[0].links[0].pasal == "151"


def test_salinan_review_tanpa_id_tidak_query(db_session):
    # Act + Assert
    assert service.review_copies(db_session, set()) == {}


def test_penulis_kasus(as_role, db_session):
    # Arrange
    case_id = _diajukan(as_role(Role.AUTHOR))

    # Act + Assert
    assert service.case_author(db_session, uuid.UUID(case_id)) == USER_ID_QA


def test_penulis_kasus_tidak_dikenal(db_session):
    # Act + Assert
    with pytest.raises(NotFoundError):
        service.case_author(db_session, uuid.uuid4())


# --- Hasil keputusan round ------------------------------------------------------


def test_disetujui_jadi_versi_yang_berlaku(as_role, db_session):
    # Arrange
    case_id = _diajukan(as_role(Role.AUTHOR))
    versi_id = _kasus(db_session, case_id).current_version_id

    # Act
    service.apply_review_decision(db_session, versi_id, approved=True)
    db_session.commit()

    # Assert
    kasus = _kasus(db_session, case_id)
    assert kasus.current_version.status == CaseStatus.APPROVED
    assert kasus.latest_approved_version_id == versi_id


def test_revisi_kembali_ke_penulis_dan_bisa_diedit(as_role, db_session):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _diajukan(client)
    kasus = _kasus(db_session, case_id)

    # Act
    service.apply_review_decision(db_session, kasus.current_version_id, approved=False)
    db_session.commit()

    # Assert
    kasus = _kasus(db_session, case_id)
    assert kasus.current_version.status == CaseStatus.NEEDS_REVISION
    assert kasus.latest_approved_version_id is None
    badan = client.get(f"/cases/{case_id}").json()
    assert client.put(f"/cases/{case_id}", json={**badan, "traps": []}).status_code == 200


def test_versi_kedua_disetujui_menggantikan_yang_lama(as_role, db_session):
    """PBI-8: v1 tetap berlaku sampai v2 disetujui, lalu v2 yang berlaku."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _diajukan(client)
    service.apply_review_decision(
        db_session, _kasus(db_session, case_id).current_version_id, approved=True
    )
    db_session.commit()
    assert client.post(f"/cases/{case_id}/versions").status_code == 201
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    v2 = _kasus(db_session, case_id).current_version_id

    # Act
    service.apply_review_decision(db_session, v2, approved=True)
    db_session.commit()

    # Assert
    assert client.get(f"/cases/{case_id}").json()["version"] == 2
    assert _kasus(db_session, case_id).latest_approved_version_id == v2


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.NEEDS_REVISION, CaseStatus.APPROVED]
)
def test_keputusan_hanya_untuk_versi_in_review(status):
    # Arrange
    versi = SimpleNamespace(id=uuid.uuid4(), status=status)
    with patch.object(service, "repository") as repo:
        repo.get_version_by_id.return_value = versi

        # Act
        with pytest.raises(ConflictError) as info:
            service.apply_review_decision(MagicMock(), versi.id, approved=True)

    # Assert
    assert info.value.code == "VERSION_NOT_IN_REVIEW"
    assert versi.status == status


def test_keputusan_tidak_commit():
    # Arrange
    db = MagicMock()
    versi = SimpleNamespace(id=uuid.uuid4(), case_id=uuid.uuid4(), status=CaseStatus.IN_REVIEW)
    kasus = SimpleNamespace(latest_approved_version_id=None)
    with patch.object(service, "repository") as repo:
        repo.get_version_by_id.return_value = versi
        repo.get_by_id.return_value = kasus

        # Act
        service.apply_review_decision(db, versi.id, approved=True)

    # Assert
    db.commit.assert_not_called()
    assert kasus.latest_approved_version_id == versi.id


def test_versi_tidak_dikenal():
    # Arrange
    with patch.object(service, "repository") as repo:
        repo.get_version_by_id.return_value = None

        # Act + Assert
        with pytest.raises(NotFoundError):
            service.apply_review_decision(MagicMock(), uuid.uuid4(), approved=False)
