"""POST /measurements/{id}/grade untuk sesi manual. PBI-22 (SCRUM-176) AC9; SCRUM-233.

Review Elision #121: SCRUM-226 (#114) sudah merge, jadi grade_session memanggil
ManualGradeGate. D7: Nilai hanya bila semua kasus dan percobaan terisi, kalau belum 409
NOT_COMPLETE. D8: setelah dinilai, Lengkap menjadi Dinilai; Selesai tetap Selesai. Sesi
otomatis tetap memakai aturan D4 dari ensure_session_gradable (diuji di test_session_grade.py).

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.grading.models import GradingRun, RubricVersion
from app.modules.runs.models import Attempt, MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN
from tests.modules.runs.test_manual_session_detail import _sesi_manual


@pytest.fixture
def manual(siap_manual):
    """Sesi manual 3 kasus x 1 percobaan (lengkap = 3 kali simpan) dan rubrik v1 aktif."""
    siap_manual.db.add(
        RubricVersion(
            version_no=1,
            effective_date=date.today(),
            is_active=True,
            locked_at=datetime.now(UTC),
            judge_prompt_quality="Prompt kualitas",
            judge_prompt_format="Prompt format",
            created_by=ADMIN_LAIN,
        )
    )
    siap_manual.db.commit()
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    percobaan = siap_manual.db.scalars(
        select(Attempt).where(Attempt.session_id == sesi_id).order_by(Attempt.id)
    ).all()
    return siap_manual, sesi_id, percobaan


def _isi(client, sesi_id, percobaan) -> None:
    """Simpan jawaban lewat PUT SCRUM-231, jadi status dihitung ulang seperti di aplikasi."""
    for attempt in percobaan:
        respons = client.put(
            f"/manual-measurements/{sesi_id}/answers",
            json={
                "snapshot_item_id": str(attempt.snapshot_item_id),
                "attempt_no": attempt.repetition_no,
                "answer_md": "Berdasarkan Pasal 156 UU 13/2003, pesangon wajib dibayar.",
                "platform_refused": False,
                "requires_confirmation": False,
            },
        )
        assert respons.status_code == 200, respons.text


def _status(db, sesi_id) -> SessionStatus:
    db.expire_all()
    return SessionStatus(db.get(MeasurementSession, sesi_id).status)


def _nilai(ctx, sesi_id):
    return ctx.sebagai(Role.ADMIN).post(f"/measurements/{sesi_id}/grade", json={})


def test_sesi_manual_terisi_sebagian_ditolak_not_complete(manual):
    """Negatif: AC9, D7, sesi manual yang masih ada jawaban kosong tidak dinilai.

    Tanpa gate, grade_session hanya mengenal status D4 dan sesi manual mendapat
    SESSION_NOT_GRADABLE, atau lebih buruk lagi dinilai dengan jawaban kosong.
    """
    # Arrange
    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan[:1])

    # Act
    respons = _nilai(ctx, sesi_id)

    # Assert
    assert respons.status_code == 409, respons.text
    assert respons.json()["code"] == "NOT_COMPLETE"
    assert ctx.db.scalars(select(GradingRun).where(GradingRun.session_id == sesi_id)).all() == []
    assert _status(ctx.db, sesi_id) is SessionStatus.PARTIALLY_FILLED


def test_sesi_manual_lengkap_dinilai_lalu_menjadi_dinilai(manual):
    """Positif: AC9, D8, Lengkap dinilai dengan pipeline yang sama, lalu berstatus Dinilai."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan)
    assert _status(ctx.db, sesi_id) is SessionStatus.COMPLETE

    # Act
    respons = _nilai(ctx, sesi_id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert {kasus["attempts"][0]["source"] for kasus in respons.json()["cases"]} == {"manual"}
    assert _status(ctx.db, sesi_id) is SessionStatus.GRADED
    perpindahan = ctx.db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == sesi_id, AuditLog.action == "measurement_session.status_changed"
        )
    ).all()
    assert any(log.after.get("status") == "graded" for log in perpindahan)


def test_nilai_ulang_sesi_manual_tidak_menulis_status_lagi(manual):
    """Edge: D8, nilai ulang dari Dinilai diterima dan statusnya tetap Dinilai."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan)
    _nilai(ctx, sesi_id)

    # Act
    respons = _nilai(ctx, sesi_id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json()["kind"] == "regrade"
    assert _status(ctx.db, sesi_id) is SessionStatus.GRADED


def test_grade_session_tanpa_now_tetap_memakai_waktu_nyata(manual):
    """Edge: review Elision #121, pemanggil tanpa now tidak meneruskan None ke after_grading."""
    # Arrange
    from app.modules.runs.session_grade import grade_session

    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan)

    # Act
    hasil = grade_session(ctx.db, sesi_id, actor_id=ADMIN_LAIN, actor_role=Role.ADMIN)

    # Assert
    assert hasil.triggered_at is not None
    assert _status(ctx.db, sesi_id) is SessionStatus.GRADED
