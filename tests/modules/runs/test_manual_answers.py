"""TDD untuk penyimpanan jawaban manual SCRUM-231."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.modules.runs.models import AnswerHistory, Attempt
from app.shared.security import Role
from tests.modules.runs.test_manual_session_detail import _sesi_manual
from tests.modules.runs.test_manual_session_endpoint import siap_manual as manual_setup


@pytest.fixture
def manual(manual_setup):
    session_id = _sesi_manual(manual_setup, repeat_count=1)
    attempts = manual_setup.db.scalars(
        select(Attempt).where(Attempt.session_id == session_id).order_by(Attempt.id)
    ).all()
    return manual_setup, session_id, attempts


def path(session_id):
    return f"/manual-measurements/{session_id}/answers"


def payload(attempt, **changes):
    data = {
        "snapshot_item_id": str(attempt.snapshot_item_id),
        "attempt_no": attempt.repetition_no,
        "answer_md": "\n# Markdown\r\n**tebal**\n\n",
        "platform_refused": False,
        "requires_confirmation": False,
    }
    data.update(changes)
    return data


def test_markdown_disimpan_tanpa_perubahan(manual):
    ctx, session_id, attempts = manual
    original = payload(attempts[0])["answer_md"] * 50

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(attempts[0], answer_md=original),
    )

    assert response.status_code == 200, response.text
    assert response.json()["answer_md"] == original
    assert response.json()["obtained_at"]

    ctx.db.expire_all()
    stored = ctx.db.get(Attempt, attempts[0].id)
    assert stored.answer_text == original
    assert stored.filled_at is not None
    assert stored.filled_by is not None


@pytest.mark.parametrize("answer", [None, "", " \t\n "])
def test_jawaban_kosong_ditolak_422(manual, answer):
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(attempts[0], answer_md=answer),
    )

    assert response.status_code == 422


def test_platform_refused_tanpa_teks_diperbolehkan(manual):
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(
            attempts[0],
            answer_md=None,
            platform_refused=True,
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["platform_refused"] is True


def test_overwrite_harus_dikonfirmasi_dan_disimpan_di_history(manual):
    ctx, session_id, attempts = manual
    client = ctx.sebagai(Role.ADMIN)
    attempt = attempts[0]

    first = payload(attempt, answer_md="Jawaban lama")
    assert client.put(path(session_id), json=first).status_code == 200

    second = payload(attempt, answer_md="Jawaban baru")

    conflict = client.put(path(session_id), json=second)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFIRMATION_REQUIRED"

    success = client.put(
        path(session_id),
        json={**second, "requires_confirmation": True},
    )
    assert success.status_code == 200, success.text

    ctx.db.expire_all()
    stored = ctx.db.get(Attempt, attempt.id)
    assert stored.answer_text == "Jawaban baru"

    history = ctx.db.scalars(
        select(AnswerHistory).where(AnswerHistory.attempt_id == attempt.id)
    ).all()
    assert len(history) == 1
    assert history[0].answer_md == "Jawaban lama"
    assert history[0].overwritten_by is not None

    response = client.get(f"{path(session_id)}/{attempt.snapshot_item_id}/1/history")
    assert response.status_code == 200
    assert response.json()[0]["answer_md"] == "Jawaban lama"


def test_waktu_diperoleh_bisa_diubah(manual):
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(
            attempts[0],
            obtained_at="2026-11-05T12:34:56+07:00",
        ),
    )

    assert response.status_code == 200, response.text
    obtained = datetime.fromisoformat(response.json()["obtained_at"])
    assert obtained.hour == 5


def test_attempt_bukan_milik_sesi_ditolak(manual):
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(
            attempts[0],
            snapshot_item_id=str(uuid.uuid4()),
        ),
    )

    assert response.status_code == 422


def test_tidak_boleh_mengubah_setelah_finalized(manual):
    ctx, session_id, attempts = manual
    client = ctx.sebagai(Role.ADMIN)

    for attempt in attempts:
        response = client.put(path(session_id), json=payload(attempt))
        assert response.status_code == 200, response.text

    finalized = client.post(f"/manual-measurements/{session_id}/finalize")
    assert finalized.status_code == 200, finalized.text

    response = client.put(
        path(session_id),
        json=payload(attempts[0], requires_confirmation=True),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "MANUAL_SESSION_FINALIZED"


@pytest.mark.parametrize("role", [Role.VIEWER, Role.DEVELOPER_AI])
def test_role_tidak_diizinkan(manual, role):
    ctx, session_id, attempts = manual
    client = ctx.sebagai(role)

    response = client.put(
        path(session_id),
        json=payload(attempts[0]),
    )

    assert response.status_code == 403
