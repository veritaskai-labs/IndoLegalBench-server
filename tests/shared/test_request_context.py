"""Test pengikat identitas pelaku ke session database.

PBI-18. Pencatat audit membaca pelaku dan request_id dari session yang
sama dengan perubahannya, bukan dari contextvar. Dependency FastAPI yang
sinkron jalan di thread terpisah, jadi contextvar yang diisi di sana
tidak terbawa ke endpoint.
"""

import uuid

from app.modules.auth.seeds import ADMIN_SUB
from app.shared import request_context
from app.shared.security import Role
from tests.login import complete_login

USER_ID = uuid.UUID("00000000-0000-0000-0000-00000000b001")


def test_session_baru_belum_punya_aktor(db_session):
    assert request_context.actor(db_session) is None
    assert request_context.request_id(db_session) is None


def test_bind_menyimpan_aktor_dan_request_id(db_session):
    request_id = uuid.uuid4()

    request_context.bind(db_session, user_id=USER_ID, role=Role.REVIEWER, request_id=request_id)

    aktor = request_context.actor(db_session)
    assert aktor.user_id == USER_ID
    assert aktor.role == "reviewer"
    assert request_context.request_id(db_session) == request_id


def test_login_asli_mengikat_aktor_ke_session(client, db_session):
    """get_current_user mengikat pelaku dan request_id ke session request itu."""
    complete_login(client, db_session, sub=ADMIN_SUB)

    response = client.get("/me")

    assert response.status_code == 200
    aktor = request_context.actor(db_session)
    assert str(aktor.user_id) == response.json()["id"]
    assert aktor.role == "admin"
    assert str(request_context.request_id(db_session)) == response.headers["X-Request-ID"]


def test_setiap_response_membawa_request_id(client):
    response = client.get("/health")

    assert uuid.UUID(response.headers["X-Request-ID"])


def test_request_id_beda_per_request(client):
    pertama = client.get("/health").headers["X-Request-ID"]
    kedua = client.get("/health").headers["X-Request-ID"]

    assert pertama != kedua
