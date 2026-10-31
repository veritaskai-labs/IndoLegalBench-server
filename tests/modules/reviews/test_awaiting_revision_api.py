"""SCRUM-242: GET /reviews/awaiting-revision dari ujung ke ujung."""

import uuid

import pytest

from app.main import app
from app.shared.security import Role, get_current_user
from tests.modules.conftest import USER_ID_QA
from tests.modules.reviews.test_review_api import (
    REVIEWER_A,
    REVIEWER_B,
    _ajukan_ulang_setelah_revisi_b,
    _nilai,
)
from tests.modules.reviews.test_review_api import (
    jadi as jadi,
)
from tests.modules.reviews.test_review_api import (
    kasus as kasus,
)

URL = "/reviews/awaiting-revision"
KOMENTAR = "Mohon perjelas rujukan."


@pytest.fixture(autouse=True)
def isolate_existing_notifications(monkeypatch):
    monkeypatch.setattr("app.modules.notifications.service.DbNotifier.notify", lambda *args: None)


@pytest.fixture
def diputuskan_revisi(kasus, jadi):
    """A meminta revisi dan B setuju, jadi round 1 decided dengan hasil revise."""
    client, case_id, ids = kasus
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    assert _nilai(client, ids[REVIEWER_A], "revise", KOMENTAR).status_code == 201
    jadi(Role.CONTRIBUTOR, REVIEWER_B)
    assert _nilai(client, ids[REVIEWER_B], "approve").status_code == 201
    return client, case_id, ids


def test_reviewer_yang_meminta_revisi_melihat_kasusnya(diputuskan_revisi, jadi):
    # Arrange
    client, case_id, ids = diputuskan_revisi
    jadi(Role.CONTRIBUTOR, REVIEWER_A)

    # Act
    response = client.get(URL)

    # Assert
    assert response.status_code == 200
    [item] = response.json()
    assert item["assignment_id"] == ids[REVIEWER_A]
    assert item["case_id"] == case_id
    assert item["title"] == "PHK sepihak"
    assert item["round_no"] == 1
    assert item["my_decision"] == "revise"
    assert item["thread_assignment_ids"] == [ids[REVIEWER_A]]


def test_reviewer_yang_setuju_melihat_kasus_dan_id_thread_rekan(diputuskan_revisi, jadi):
    # Arrange
    client, _, ids = diputuskan_revisi
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    [item] = client.get(URL).json()

    # Assert
    assert item["assignment_id"] == ids[REVIEWER_B]
    assert item["my_decision"] == "approve"
    assert item["thread_assignment_ids"] == [ids[REVIEWER_A]]
    assert client.get(f"/reviews/{item['thread_assignment_ids'][0]}/discussion").status_code == 200


def test_respons_antrean_tidak_memuat_komentar_atau_verdict_rekan(diputuskan_revisi, jadi):
    # Arrange
    client, _, _ = diputuskan_revisi
    jadi(Role.CONTRIBUTOR, REVIEWER_B)

    # Act
    mentah = client.get(URL).text

    # Assert
    assert KOMENTAR not in mentah


def test_kasus_keluar_dari_antrean_setelah_penulis_mengajukan_ulang(kasus, jadi):
    # Arrange
    client, case_id, ids = kasus
    _ajukan_ulang_setelah_revisi_b(client, jadi, case_id, ids)

    # Act
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    antrean_a = client.get(URL).json()
    jadi(Role.CONTRIBUTOR, REVIEWER_B)
    antrean_b = client.get(URL).json()

    # Assert
    assert antrean_a == []
    assert antrean_b == []


def test_kasus_disetujui_tidak_ada_di_antrean(kasus, jadi):
    # Arrange
    client, _, ids = kasus
    for reviewer in (REVIEWER_A, REVIEWER_B):
        jadi(Role.CONTRIBUTOR, reviewer)
        assert _nilai(client, ids[reviewer], "approve").status_code == 201

    # Act + Assert
    assert client.get(URL).json() == []
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    assert client.get(URL).json() == []


def test_round_yang_belum_diputuskan_tidak_ada_di_antrean(kasus, jadi):
    # Arrange
    client, _, ids = kasus
    jadi(Role.CONTRIBUTOR, REVIEWER_A)
    _nilai(client, ids[REVIEWER_A], "revise", KOMENTAR)

    # Act + Assert
    assert client.get(URL).json() == []


def test_reviewer_tanpa_kasus_mendapat_daftar_kosong(diputuskan_revisi, jadi):
    # Arrange
    client, _, _ = diputuskan_revisi
    jadi(Role.CONTRIBUTOR, uuid.uuid4())

    # Act + Assert
    assert client.get(URL).json() == []


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ADMIN])
def test_peran_tanpa_wewenang_review_ditolak(diputuskan_revisi, jadi, role):
    # Arrange
    client, _, _ = diputuskan_revisi
    jadi(role, uuid.uuid4())

    # Act + Assert
    assert client.get(URL).status_code == 403


def test_tanpa_sesi_ditolak(diputuskan_revisi):
    # Arrange
    client, _, _ = diputuskan_revisi
    app.dependency_overrides.pop(get_current_user)

    # Act + Assert
    assert client.get(URL).status_code == 401


def test_penulis_kasus_tanpa_peran_reviewer_ditolak(diputuskan_revisi, jadi):
    # Arrange
    client, _, _ = diputuskan_revisi
    jadi(Role.VIEWER, USER_ID_QA)

    # Act + Assert
    assert client.get(URL).status_code == 403
