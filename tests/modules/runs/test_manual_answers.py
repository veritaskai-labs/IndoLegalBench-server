"""TDD untuk penyimpanan jawaban manual SCRUM-231."""

import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.modules.runs.models import AnswerHistory, Attempt, AttemptSource, SessionStatus
from app.modules.runs.schemas import ManualAnswerResponse
from app.shared.security import Role
from tests.modules.runs.test_manual_session_detail import _sesi_manual

pytest_plugins = ("tests.modules.runs.test_manual_session_endpoint",)


@pytest.fixture
def manual(siap_manual):
    session_id = _sesi_manual(siap_manual, repeat_count=1)
    attempts = siap_manual.db.scalars(
        select(Attempt).where(Attempt.session_id == session_id).order_by(Attempt.id)
    ).all()

    return siap_manual, session_id, attempts


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
            note="Platform menolak menjawab",
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
    """Negatif: kasus di luar snapshot sesi ditolak dengan kodenya sendiri."""
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(
            attempts[0],
            snapshot_item_id=str(uuid.uuid4()),
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "MANUAL_CASE_NOT_IN_SNAPSHOT"


def test_nomor_percobaan_di_luar_pengulangan_sesi_ditolak(manual):
    """Negatif: sesi ini satu pengulangan, jadi percobaan kedua tidak ada (AC4).

    Kodenya harus beda dari kasus di luar snapshot, karena tiket menyebut keduanya
    sebagai syarat terpisah.
    """
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(attempts[0], attempt_no=2),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "MANUAL_ATTEMPT_NO_OUT_OF_RANGE"


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


@pytest.mark.parametrize("note", [None, "", " \t\n"])
def test_refusal_must_include_note(manual, note):
    ctx, session_id, attempts = manual

    response = ctx.sebagai(Role.ADMIN).put(
        path(session_id),
        json=payload(
            attempts[0],
            answer_md=None,
            platform_refused=True,
            note=note,
        ),
    )

    assert response.status_code == 422, response.text


def test_save_recomputes_progress_and_date_range(manual):
    ctx, session_id, attempts = manual
    client = ctx.sebagai(Role.ADMIN)

    dates = [
        "2026-11-05T12:00:00+07:00",
        "2026-11-07T09:00:00+07:00",
        "2026-11-06T10:00:00+07:00",
    ]

    assert len(attempts) == len(dates)

    for i, attempt in enumerate(attempts):
        response = client.put(
            path(session_id),
            json=payload(attempt, obtained_at=dates[i]),
        )

        assert response.status_code == 200, response.text

        status = "complete" if i == len(attempts) - 1 else "partially_filled"

        assert response.json()["session_status"] == status

        detail_response = client.get(f"/manual-measurements/{session_id}")

        assert detail_response.status_code == 200
        detail = detail_response.json()

        assert detail["status"] == status
        assert detail["filled_answers"] == i + 1

    assert (
        datetime.fromisoformat(detail["answer_date_from"]).timestamp()
        == datetime.fromisoformat(dates[0]).timestamp()
    )

    assert (
        datetime.fromisoformat(detail["answer_date_to"]).timestamp()
        == datetime.fromisoformat(dates[1]).timestamp()
    )


def test_waktu_jawaban_manual_keluar_sebagai_iso_8601_utc():
    """Edge: waktu di respons wajib berakhiran Z, supaya FE tahu itu UTC (AC7).

    Tes suite memakai SQLite yang membuang zona waktu, jadi tanpa anotasi
    UtcDateTime nilainya keluar polos tanpa penanda dan FE salah menghitung WIB.
    """
    polos = datetime(2026, 11, 7, 14, 0)

    jawaban = ManualAnswerResponse(
        attempt_id=uuid.uuid4(),
        snapshot_item_id=uuid.uuid4(),
        attempt_no=1,
        source=AttemptSource.MANUAL,
        answer_md="**jawaban**",
        obtained_at=polos,
        platform_refused=False,
        note=None,
        filled_by=uuid.uuid4(),
        filled_at=polos,
        session_status=SessionStatus.PARTIALLY_FILLED,
    )

    terkirim = json.loads(jawaban.model_dump_json())

    assert terkirim["obtained_at"] == "2026-11-07T14:00:00Z"
    assert terkirim["filled_at"] == "2026-11-07T14:00:00Z"


def test_riwayat_memuat_penimpa_dan_waktu_timpa_dalam_utc(manual):
    """Positif: riwayat wajib menyebut siapa yang menimpa dan kapan, ISO 8601 UTC (AC5).

    Dua field ini mudah hilang tanpa terdeteksi: ruff tidak mengeluh kalau nama
    field Pydantic kembar, dan tes lain hanya memeriksa baris database, bukan
    respons HTTP-nya.
    """
    ctx, session_id, attempts = manual
    client = ctx.sebagai(Role.ADMIN)
    attempt = attempts[0]

    client.put(path(session_id), json=payload(attempt, answer_md="Jawaban lama"))
    client.put(
        path(session_id),
        json=payload(attempt, answer_md="Jawaban baru", requires_confirmation=True),
    )

    response = client.get(f"{path(session_id)}/{attempt.snapshot_item_id}/1/history")

    assert response.status_code == 200, response.text
    catatan = response.json()[0]
    assert catatan["overwritten_by"]
    assert catatan["overwritten_at"].endswith("Z")
    assert catatan["filled_at"].endswith("Z")
