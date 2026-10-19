"""Nama suite untuk kolom Objek audit log (F-06, TC-31, PBI-18 AC4)."""

import uuid

from app.modules.suites import service
from app.shared.security import Role


def _suite(client, nama: str) -> uuid.UUID:
    response = client.post("/suites", json={"name": nama})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def test_nama_suite_per_id(as_role, db_session):
    client = as_role(Role.AUTHOR)
    pidana, perdata = _suite(client, "Pidana"), _suite(client, "Perdata")

    assert service.suite_names(db_session, {pidana, perdata}) == {
        pidana: "Pidana",
        perdata: "Perdata",
    }


def test_suite_yang_sudah_dihapus_tetap_bernama(as_role, db_session):
    """Catatan suite.deleted tetap harus terbaca setelah suitenya dihapus."""
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client, "Lama")
    assert client.delete(f"/suites/{suite_id}").status_code == 204

    assert service.suite_names(db_session, {suite_id}) == {suite_id: "Lama"}


def test_id_tak_dikenal_tidak_ikut(db_session):
    assert service.suite_names(db_session, {uuid.uuid4()}) == {}


def test_tanpa_id_tanpa_query(db_session):
    assert service.suite_names(db_session, set()) == {}
