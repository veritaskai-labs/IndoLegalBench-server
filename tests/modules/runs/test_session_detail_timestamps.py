"""GET /measurements/{id}: waktu mulai dan selesai per percobaan. SCRUM-76 SCRUM-259.

Waktu dikirim ISO 8601 UTC; tampilan WIB diatur FE. Data lama dan percobaan yang
belum jalan memberi null, bukan tebakan.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.modules.runs.models import Attempt, AttemptStatus, SessionStatus
from app.shared.security import Role
from tests.modules.runs.test_session_detail_endpoint import (
    JAWABAN_TEST,
    PERTANYAAN_TEST,
    _body,
    _kasus,
    _snapshot,
    pembuat,
    produk,
)
from tests.modules.runs.test_session_list_endpoint import _sesi

__all__ = ["pembuat", "produk"]

MULAI = datetime(2026, 11, 6, 3, 15, 0, tzinfo=UTC)
SELESAI = MULAI + timedelta(seconds=42)


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}"


def _stempel(db_session, item, nomor: int, mulai, selesai) -> None:
    db_session.execute(
        update(Attempt)
        .where(Attempt.snapshot_item_id == item.id, Attempt.repetition_no == nomor)
        .values(started_at=mulai, finished_at=selesai)
    )
    db_session.commit()


def _sesi_dengan_waktu(db_session, produk, pembuat, *, status=SessionStatus.FINISHED):
    sesi = _sesi(db_session, produk, pembuat, label="Run Waktu", status=status)
    snapshot = _snapshot(db_session, sesi)
    dev = _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-001", "dev", "Pertanyaan dev"),
        [
            (AttemptStatus.SUCCEEDED, "Jawaban dev"),
            (AttemptStatus.TECHNICAL_FAILURE, None),
            (AttemptStatus.PENDING, None),
        ],
    )
    test = _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-002", "test", PERTANYAAN_TEST),
        [(AttemptStatus.SUCCEEDED, JAWABAN_TEST)],
    )
    _stempel(db_session, dev, 1, MULAI, SELESAI)
    _stempel(db_session, dev, 2, MULAI, SELESAI + timedelta(seconds=5))
    _stempel(db_session, test, 1, MULAI, SELESAI)
    return sesi


def _per_kode(body: dict) -> dict:
    return {item["case_code"]: item for item in body["cases"]}


def _waktu(teks: str) -> datetime:
    return datetime.fromisoformat(teks.replace("Z", "+00:00"))


def test_attempt_timestamps_are_iso_8601_utc_for_success_and_failure(
    as_role, db_session, produk, pembuat
):
    """Positif: sukses dan gagal teknis sama-sama punya waktu, dalam UTC bertanda zona."""
    # Arrange
    sesi = _sesi_dengan_waktu(db_session, produk, pembuat)

    # Act
    response = as_role(Role.ADMIN).get(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    sukses, gagal, _ = _per_kode(response.json())["KASUS-001"]["attempts"]
    assert sukses["status"] == "succeeded"
    assert _waktu(sukses["started_at"]) == MULAI
    assert _waktu(sukses["finished_at"]) == SELESAI
    assert sukses["started_at"].endswith(("Z", "+00:00"))
    assert gagal["status"] == "technical_failure"
    assert _waktu(gagal["started_at"]) == MULAI
    assert _waktu(gagal["finished_at"]) == SELESAI + timedelta(seconds=5)
    assert _waktu(gagal["finished_at"]).utcoffset() == timedelta(0)


def test_pending_and_legacy_attempts_report_null_timestamps(as_role, db_session, produk, pembuat):
    """Edge: percobaan yang belum jalan atau data lama memberi null, kuncinya tetap ada."""
    # Arrange
    sesi = _sesi_dengan_waktu(db_session, produk, pembuat)

    # Act
    body = as_role(Role.ADMIN).get(_url(sesi.id)).json()

    # Assert
    belum = _per_kode(body)["KASUS-001"]["attempts"][2]
    assert belum["status"] == "pending"
    assert "started_at" in belum and "finished_at" in belum
    assert (belum["started_at"], belum["finished_at"]) == (None, None)


def test_running_attempt_has_start_but_no_finish(as_role, db_session, produk, pembuat):
    """Edge: percobaan yang sedang dipanggil sudah punya waktu mulai, belum selesai."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, label="Run Jalan", status=SessionStatus.RUNNING)
    item = _kasus(
        db_session,
        sesi,
        _snapshot(db_session, sesi),
        _body("KASUS-001", "dev", "Pertanyaan dev"),
        [(AttemptStatus.RUNNING, None)],
    )
    _stempel(db_session, item, 1, MULAI, None)

    # Act
    body = as_role(Role.ADMIN).get(_url(sesi.id)).json()

    # Assert
    jalan = body["cases"][0]["attempts"][0]
    assert _waktu(jalan["started_at"]) == MULAI
    assert jalan["finished_at"] is None


def test_developer_ai_sees_timestamps_but_not_masked_test_answer(
    as_role, db_session, produk, pembuat
):
    """Negatif: kasus test tetap disamarkan; waktu tidak memuat isi kasus jadi tetap tampil."""
    # Arrange
    sesi = _sesi_dengan_waktu(db_session, produk, pembuat)

    # Act
    body = as_role(Role.DEVELOPER_AI).get(_url(sesi.id)).json()

    # Assert
    test = next(item for item in body["cases"] if item["masked"])
    assert test["masked"] is True
    assert test["question"] is None
    percobaan = test["attempts"][0]
    assert percobaan["answer_text"] is None
    assert _waktu(percobaan["started_at"]) == MULAI
    assert _waktu(percobaan["finished_at"]) == SELESAI
