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


class _JudgeGema:
    """Judge palsu yang menjawab MET: yes untuk setiap kriteria di prompt.

    Sejak #128 sesi tanpa penilai tidak mendapat skor sama sekali (judge_not_configured), jadi
    tanpa judge ini setiap percobaan sudah gangguan teknis dan tes penolakan tidak membuktikan apa pun.
    """

    error = None
    failure_kind = None
    input_tokens = 25
    output_tokens = 30
    text = ""

    def ask(self, prompt: str):
        kriteria = prompt.split("Criteria:\n", 1)[1].split("\n\nGround truth:", 1)[0]
        self.text = "".join(
            f"CRITERION: {baris.strip()}\nMET: yes\n"
            for baris in kriteria.splitlines()
            if baris.strip()
        )
        return self


@pytest.fixture
def manual(siap_manual, monkeypatch):
    """Sesi manual 3 kasus x 1 percobaan (lengkap = 3 kali simpan) dan rubrik v1 aktif.

    Penilai default-nya _JudgeGema, juga lewat endpoint; judge_asker dari tes tetap didahulukan.
    """
    from app.modules.runs import session_grade

    monkeypatch.setattr(
        session_grade,
        "_resolve_active_judge_asker",
        lambda db, session, judge_asker: (judge_asker or _JudgeGema(), None, None),
    )
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
                "confirm": False,
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


class _JudgeDenganSelingan(_JudgeGema):
    """Judge palsu yang menjalankan tulisan pesaing sekali, di tengah penilaian.

    Itu celah yang ditunjuk review Elision #121: jawaban sudah dimuat, judge berjalan, kunci
    sesi belum diambil. Balasannya tetap sah, jadi run yang lolos cek ulang benar-benar berskor.
    """

    def __init__(self, selingan) -> None:
        self._selingan = selingan
        self.dipanggil = 0

    def ask(self, prompt: str):
        self.dipanggil += 1
        if self.dipanggil == 1:
            self._selingan()
        return super().ask(prompt)


def _timpa(client, sesi_id, attempt, jawaban: str, *, menolak: bool = False) -> None:
    respons = client.put(
        f"/manual-measurements/{sesi_id}/answers",
        json={
            "snapshot_item_id": str(attempt.snapshot_item_id),
            "attempt_no": attempt.repetition_no,
            "answer_md": jawaban,
            "platform_refused": menolak,
            "note": "Platform menolak setelah memberi teks umum." if menolak else None,
            "confirm": True,
        },
    )
    assert respons.status_code == 200, respons.text


def _nilai_dengan(ctx, sesi_id, selingan) -> _JudgeDenganSelingan:
    from app.modules.runs.session_grade import grade_session

    judge = _JudgeDenganSelingan(selingan)
    grade_session(ctx.db, sesi_id, actor_id=ADMIN_LAIN, actor_role=Role.ADMIN, judge_asker=judge)
    return judge


def _jumlah_run(db, sesi_id) -> int:
    return len(db.scalars(select(GradingRun).where(GradingRun.session_id == sesi_id)).all())


def test_jawaban_ditimpa_saat_judge_berjalan_tidak_menyimpan_hasil(manual):
    """Negatif: review Elision #121, hasil penilaian tidak pernah tersimpan untuk jawaban lama.

    Kontributor menimpa satu jawaban lewat PUT sungguhan saat judge berjalan. Sebelum hasil
    disimpan, isi jawaban dibaca ulang di bawah kunci sesi; karena berbeda, 409 dan tidak ada
    grading run, status tetap Lengkap, dan jawaban baru tetap ada.
    """
    # Arrange
    from app.shared.exceptions import ConflictError

    ctx, sesi_id, percobaan = manual
    kontributor = ctx.sebagai(Role.CONTRIBUTOR)
    _isi(kontributor, sesi_id, percobaan)
    jawaban_baru = "Revisi: pesangon dihitung menurut PP 35/2021."

    # Act
    with pytest.raises(ConflictError) as galat:
        _nilai_dengan(
            ctx, sesi_id, lambda: _timpa(kontributor, sesi_id, percobaan[0], jawaban_baru)
        )

    # Assert
    assert galat.value.code == "ANSWERS_CHANGED_DURING_GRADING"
    assert _jumlah_run(ctx.db, sesi_id) == 0
    assert _status(ctx.db, sesi_id) is SessionStatus.COMPLETE
    assert ctx.db.get(Attempt, percobaan[0].id).answer_text == jawaban_baru


def test_sesi_diarsipkan_saat_judge_berjalan_tidak_menjadi_dinilai(manual):
    """Negatif: review Elision #121, arsip di tengah penilaian dicek ulang sebelum commit.

    Admin lain menandai Selesai lalu mengarsipkan lewat endpoint sungguhan saat judge berjalan.
    before_grading sudah lolos, jadi tanpa cek ulang hasilnya tetap tersimpan untuk sesi arsip.
    """
    # Arrange
    from app.shared.exceptions import ConflictError

    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan)
    admin = ctx.sebagai(Role.ADMIN)

    def selesai_lalu_arsip() -> None:
        assert admin.post(f"/manual-measurements/{sesi_id}/finalize").status_code == 200
        assert admin.patch(f"/measurements/{sesi_id}/archive").status_code == 200

    # Act
    with pytest.raises(ConflictError) as galat:
        _nilai_dengan(ctx, sesi_id, selesai_lalu_arsip)

    # Assert
    assert galat.value.code == "SESSION_ARCHIVED"
    assert _jumlah_run(ctx.db, sesi_id) == 0
    assert _status(ctx.db, sesi_id) is SessionStatus.FINALIZED


def test_simpan_ulang_isi_yang_sama_saat_judge_berjalan_tetap_dinilai(manual):
    """Edge: yang dibandingkan isi jawaban, bukan waktu simpan, jadi isi sama tetap dinilai.

    Hasil judge tetap sesuai jawaban yang tersimpan, jadi menolaknya hanya memaksa Admin
    memicu ulang tanpa alasan.
    """
    # Arrange
    ctx, sesi_id, percobaan = manual
    kontributor = ctx.sebagai(Role.CONTRIBUTOR)
    _isi(kontributor, sesi_id, percobaan)
    sama = ctx.db.get(Attempt, percobaan[0].id).answer_text

    # Act
    judge = _nilai_dengan(ctx, sesi_id, lambda: _timpa(kontributor, sesi_id, percobaan[0], sama))

    # Assert
    assert judge.dipanggil >= 1
    assert _jumlah_run(ctx.db, sesi_id) == 1
    assert _status(ctx.db, sesi_id) is SessionStatus.GRADED


def _percobaan_dinilai(respons, attempt_id) -> dict:
    return next(
        att
        for kasus in respons.json()["cases"]
        for att in kasus["attempts"]
        if att["attempt_id"] == str(attempt_id)
    )


def test_penolakan_dengan_teks_jawaban_sendiri_dihitung_gangguan_teknis(manual):
    """Negatif: QA PBI-22 (review Elision #121), kolom platform_refused dibaca saat menilai.

    validate_answer mengizinkan platform_refused true bersama answer_md yang berisi teks, jadi
    slotnya terisi dan sesi menjadi Lengkap. Dulu penilaian hanya mencari penanda teks yang tidak
    pernah ditulis aplikasi, jadi jawaban ini dinilai seperti jawaban biasa. AC4 PBI-14:
    penolakan platform adalah gangguan teknis, tanpa skor.
    """
    # Arrange
    ctx, sesi_id, percobaan = manual
    kontributor = ctx.sebagai(Role.CONTRIBUTOR)
    _isi(kontributor, sesi_id, percobaan)
    # Teks substantif, jadi heuristik penolakan dari teks (is_ai_refusal) tidak ikut menandainya.
    teks = "Berdasarkan Pasal 156 UU 13/2003, pesangon wajib dibayar."
    _timpa(kontributor, sesi_id, percobaan[0], teks, menolak=True)
    assert _status(ctx.db, sesi_id) is SessionStatus.COMPLETE

    # Act
    respons = _nilai(ctx, sesi_id)

    # Assert
    assert respons.status_code == 200, respons.text
    ditolak = _percobaan_dinilai(respons, percobaan[0].id)
    assert ditolak["is_technical_disruption"] is True
    assert ditolak["score"] is None
    assert _percobaan_dinilai(respons, percobaan[1].id)["is_technical_disruption"] is False


def test_penolakan_dicentang_saat_judge_berjalan_tidak_menyimpan_hasil(manual):
    """Negatif: review Elision #121, platform_refused ikut dicek ulang sebelum hasil disimpan.

    Teks jawabannya sama, hanya opsi penolakan yang dicentang. Judge sudah menilai jawaban itu
    sebagai jawaban biasa, padahal sekarang harus gangguan teknis, jadi hasilnya tidak disimpan.
    """
    # Arrange
    from app.shared.exceptions import ConflictError

    ctx, sesi_id, percobaan = manual
    kontributor = ctx.sebagai(Role.CONTRIBUTOR)
    _isi(kontributor, sesi_id, percobaan)
    sama = ctx.db.get(Attempt, percobaan[0].id).answer_text

    # Act
    with pytest.raises(ConflictError) as galat:
        _nilai_dengan(
            ctx, sesi_id, lambda: _timpa(kontributor, sesi_id, percobaan[0], sama, menolak=True)
        )

    # Assert
    assert galat.value.code == "ANSWERS_CHANGED_DURING_GRADING"
    assert _jumlah_run(ctx.db, sesi_id) == 0


def test_jawaban_berisi_penanda_penolakan_tidak_memicu_konflik_palsu(manual):
    """Edge: penanda lama "[Platform menolak menjawab]" di teks tetap dibaca sama di dua sisi.

    Cek ulang memakai turunan yang sama dengan penilaian, bukan kolom mentah. Kalau tidak, jawaban
    yang kebetulan memuat penanda itu selalu dianggap berubah dan sesinya tidak pernah bisa dinilai.
    """
    # Arrange
    ctx, sesi_id, percobaan = manual
    kontributor = ctx.sebagai(Role.CONTRIBUTOR)
    _isi(kontributor, sesi_id, percobaan)
    _timpa(kontributor, sesi_id, percobaan[0], "[Platform menolak menjawab] lalu teks umum.")

    # Act
    respons = _nilai(ctx, sesi_id)

    # Assert
    assert respons.status_code == 200, respons.text
    assert _percobaan_dinilai(respons, percobaan[0].id)["is_technical_disruption"] is True
    assert _status(ctx.db, sesi_id) is SessionStatus.GRADED


def test_run_tanpa_skor_tidak_menjadikan_sesi_dinilai(manual):
    """Negatif: D8 dengan #128, run gagal bukan hasil penilaian, jadi sesi tetap Lengkap.

    Penilai gagal untuk setiap percobaan, jadi run berstatus failed dan tidak menjadi hasil
    terbaru. Sesi yang tetap Lengkap bisa dinilai lagi; Dinilai tanpa hasil akan menyesatkan.
    """
    # Arrange
    from app.modules.runs.session_grade import grade_session

    class JudgeGagal:
        def ask(self, prompt: str):
            raise RuntimeError("penyedia penilai tidak tersedia")

    ctx, sesi_id, percobaan = manual
    _isi(ctx.sebagai(Role.CONTRIBUTOR), sesi_id, percobaan)

    # Act
    hasil = grade_session(
        ctx.db, sesi_id, actor_id=ADMIN_LAIN, actor_role=Role.ADMIN, judge_asker=JudgeGagal()
    )

    # Assert
    assert hasil.status == "failed"
    assert _status(ctx.db, sesi_id) is SessionStatus.COMPLETE
