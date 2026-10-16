"""Aturan tag dev/test lewat HTTP. PBI-9 AC1-AC3, SCRUM-164.

D2 dan D5a: sebelum disetujui tag milik penulis (PUT), sesudah disetujui
hanya Admin lewat PATCH /cases/{id}/split-tag, tercatat di audit, dan
perubahan dev ke test diberi peringatan.
"""

import uuid

import pytest
from fastapi import Depends

from app.main import app
from app.modules.audit.models import AuditLog
from app.shared import request_context
from app.shared.database import get_db
from app.shared.security import Role, get_current_user
from tests.modules.cases.test_cases import ADMIN_LAIN, _badan, _buat, _ganti, _setujui, _suite


def _jadi(pengguna) -> None:
    """Ganti pengguna dan ikat pelakunya ke session, supaya audit mencatat orang yang benar."""

    def pengguna_palsu(db=Depends(get_db)):
        request_context.bind(db, user_id=pengguna.user_id, role=pengguna.role, request_id=None)
        return pengguna

    app.dependency_overrides[get_current_user] = pengguna_palsu


def _ubah(client, case_id: str, tag: str, **lain):
    return client.patch(f"/cases/{case_id}/split-tag", json={"split_tag": tag, **lain})


@pytest.fixture
def disetujui(as_role, buat_pengguna, db_session):
    """Kasus dev yang sudah disetujui, dengan Admin yang login."""
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _setujui(db_session, case_id)
    _jadi(buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))
    return client, case_id


def test_dev_ke_test_meminta_konfirmasi_dulu(disetujui):
    # Arrange
    client, case_id = disetujui

    # Act
    response = _ubah(client, case_id, "test")

    # Assert
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "TAG_CHANGE_NEEDS_CONFIRMATION"
    assert body["requires_confirmation"] is True
    assert "developer AI" in body["message"]
    assert client.get(f"/cases/{case_id}").json()["split_tag"] == "dev"


def test_dev_ke_test_terkonfirmasi_dan_ditandai_pernah_dev(disetujui):
    # Arrange
    client, case_id = disetujui

    # Act
    response = _ubah(client, case_id, "test", confirm=True)

    # Assert
    assert response.status_code == 200
    assert response.json()["split_tag"] == "test"
    assert response.json()["was_dev"] is True
    assert response.json()["status"] == "approved"


def test_perubahan_tercatat_di_audit_dengan_peringatan(disetujui, db_session):
    # Arrange
    client, case_id = disetujui

    # Act
    _ubah(client, case_id, "test", confirm=True)

    # Assert
    [baris] = db_session.query(AuditLog).filter_by(action="case.tag_changed").all()
    assert str(baris.case_id) == case_id
    assert baris.before == {"split_tag": "dev"}
    assert baris.after == {"split_tag": "test", "warning": True}
    assert baris.actor_user_id == ADMIN_LAIN


def test_penanda_pernah_dev_tetap_setelah_kembali_ke_dev(disetujui):
    # Arrange
    client, case_id = disetujui
    _ubah(client, case_id, "test", confirm=True)

    # Act
    response = _ubah(client, case_id, "dev")

    # Assert
    assert response.json()["split_tag"] == "dev"
    assert response.json()["was_dev"] is True


def test_draf_versi_berikutnya_ikut_berganti_tag(disetujui):
    # Arrange
    client, case_id = disetujui
    assert client.post(f"/cases/{case_id}/versions").status_code == 201

    # Act
    _ubah(client, case_id, "test", confirm=True)

    # Assert
    assert client.get(f"/cases/{case_id}/versions/current").json()["split_tag"] == "test"
    assert client.get(f"/cases/{case_id}").json()["split_tag"] == "test"


@pytest.mark.parametrize("role", [Role.AUTHOR, Role.REVIEWER, Role.VIEWER])
def test_hanya_admin_yang_boleh(disetujui, buat_pengguna, role):
    # Arrange
    client, case_id = disetujui
    _ganti(client, buat_pengguna(role, user_id=uuid.uuid4()))

    # Act
    response = _ubah(client, case_id, "test", confirm=True)

    # Assert
    assert response.status_code == 403


def test_kasus_belum_disetujui_ditolak(as_role, buat_pengguna):
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]
    _ganti(client, buat_pengguna(Role.ADMIN, user_id=ADMIN_LAIN))

    # Act
    response = _ubah(client, case_id, "test", confirm=True)

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] == "NO_APPROVED_VERSION"


def test_tag_tidak_dikenal_ditolak(disetujui):
    # Arrange
    client, case_id = disetujui

    # Act + Assert
    assert _ubah(client, case_id, "holdout").status_code == 422


def test_kasus_tidak_ada(disetujui):
    # Arrange
    client, _ = disetujui

    # Act + Assert
    assert _ubah(client, str(uuid.uuid4()), "test", confirm=True).status_code == 404


def test_penulis_tetap_mengubah_tag_lewat_put_sebelum_disetujui(as_role):
    """AC3 bagian pertama, sudah ada sejak PBI-3: tetap berlaku."""
    # Arrange
    client = as_role(Role.AUTHOR)
    case_id = _buat(client, _suite(client)).json()["id"]

    # Act
    response = client.put(f"/cases/{case_id}", json=_badan(split_tag="test"))

    # Assert
    assert response.status_code == 200
    assert response.json()["split_tag"] == "test"
    assert response.json()["was_dev"] is False
