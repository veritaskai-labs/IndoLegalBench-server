"""Jejak audit jawaban manual dan label manual. PBI-22 (SCRUM-176) AC9; SCRUM-233.

Tiket: jawaban dan hasil penilaian manual tercatat di jejak audit (PBI-18-BE-1), berlabel
manual di sesi, laporan, dan penelusuran. D9: "Jawaban & penilaian manual tercatat di
audit_logs" dengan aksi answer_saved / answer_overwritten, dan label manual (source)
selalu terbawa ke sesi, laporan, dan penelusuran.

AttemptTracker (pola Strategy di audit/trackers.py) memantau attempts.filled_at, jadi
penyimpanan jawaban SCRUM-231 tercatat tanpa mengubah kodenya:
- terisi pertama kali  -> measurement_session.answer_saved
- ditimpa (konfirmasi) -> measurement_session.answer_overwritten
Catatannya di sesi (entity measurement_session), karena itulah yang ditelusuri Admin;
percobaan mana yang diisi ada di kolom after. Isi jawaban dan catatan penolakan tidak
disalin ke audit: versi lamanya sudah utuh di answer_history (SCRUM-231).

Label: Positif = perilaku yang diharapkan, Negatif = tidak dicatat, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.trackers import Change, tracker_for
from app.modules.runs.models import Attempt, AttemptSource
from app.shared.security import Role
from tests.modules.runs.test_manual_session_detail import _sesi_manual

SESI = uuid.UUID("00000000-0000-0000-0000-0000000000e5")
PERCOBAAN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ITEM = uuid.UUID("00000000-0000-0000-0000-00000000000a")
PENGISI = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
HARI_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
HARI_2 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
JAWABAN_RAHASIA = "**Pasal 156 ayat (2) UU 13/2003** jawaban rahasia"


def _percobaan(source=AttemptSource.MANUAL, **kolom):
    isi = {
        "id": PERCOBAAN,
        "session_id": SESI,
        "snapshot_item_id": ITEM,
        "repetition_no": 2,
        "source": source,
        "answer_text": JAWABAN_RAHASIA,
        "manual_note": "catatan rahasia",
        "platform_refused": False,
        "answered_at_platform": HARI_1,
        "filled_by": PENGISI,
        "filled_at": HARI_2,
    }
    isi.update(kolom)
    return SimpleNamespace(**isi)


@pytest.fixture
def tracker():
    tracker = tracker_for("attempts")
    assert tracker is not None, "attempts belum punya tracker"
    return tracker


def test_jawaban_pertama_dicatat_answer_saved_di_sesinya(tracker):
    """Positif: AC9, D9 answer_saved, dicatat di sesi dengan label manual."""
    # Act
    (event,) = tracker.updated(_percobaan(), {"filled_at": Change(None, HARI_2)})

    # Assert
    assert tracker.entity_type == AuditEntityType.MEASUREMENT_SESSION
    assert event.action == "measurement_session.answer_saved"
    assert event.entity_id == SESI
    assert event.before is None
    assert event.after == {
        "attempt_id": str(PERCOBAAN),
        "snapshot_item_id": str(ITEM),
        "repetition_no": 2,
        "source": "manual",
        "platform_refused": False,
        "answered_at_platform": HARI_1.isoformat(),
        "filled_by": str(PENGISI),
        "filled_at": HARI_2.isoformat(),
    }


def test_jawaban_ditimpa_dicatat_answer_overwritten(tracker):
    """Positif: AC9, D8 timpa jawaban dengan konfirmasi; waktu isian lama di before."""
    # Act
    (event,) = tracker.updated(
        _percobaan(platform_refused=True), {"filled_at": Change(HARI_1, HARI_2)}
    )

    # Assert
    assert event.action == "measurement_session.answer_overwritten"
    assert event.before == {"filled_at": HARI_1.isoformat()}
    assert event.after["platform_refused"] is True


def test_isi_jawaban_dan_catatan_tidak_masuk_audit(tracker):
    """Negatif: PBI-18, isi jawaban ada di attempts dan answer_history, bukan audit_logs."""
    # Act
    (event,) = tracker.updated(_percobaan(), {"filled_at": Change(HARI_1, HARI_2)})

    # Assert
    teks = repr((event.before, event.after))
    assert "rahasia" not in teks


def test_percobaan_otomatis_tidak_dicatat_tracker_ini(tracker):
    """Negatif: jawaban otomatis sudah dibuktikan call_logs (SCRUM-209)."""
    # Act / Assert
    assert (
        tracker.updated(
            _percobaan(source=AttemptSource.AUTOMATIC), {"filled_at": Change(None, HARI_2)}
        )
        == []
    )


def test_percobaan_baru_dan_status_tidak_dicatat(tracker):
    """Negatif: percobaan kosong dibuat bersama snapshot sesi; hanya kolom jawaban dipantau."""
    # Act / Assert
    assert tracker.created(_percobaan(filled_at=None)) == []
    assert set(tracker.fields) == {
        "filled_at",
        "filled_by",
        "answer_text",
        "manual_note",
        "platform_refused",
        "answered_at_platform",
    }


def test_isi_berubah_tanpa_filled_at_baru_tetap_tercatat_ditimpa(tracker):
    """Edge: review Elision #121, timpa tidak bergantung pada filled_at yang ikut berubah.

    SCRUM-231 selalu menulis filled_at baru, tetapi jalur tulis lain (atau dua simpan dengan
    waktu yang sama) bisa mengubah isi jawaban tanpa itu; perubahannya tetap harus diaudit.
    """
    # Act
    (event,) = tracker.updated(_percobaan(), {"answer_text": Change("jawaban lama", "baru")})

    # Assert
    assert event.action == "measurement_session.answer_overwritten"
    assert event.before == {"filled_at": HARI_2.isoformat()}
    assert "rahasia" not in repr((event.before, event.after))


def test_isi_berubah_pada_percobaan_yang_belum_terisi_tidak_dicatat(tracker):
    """Negatif: review Elision #121, tanpa filled_at belum ada jawaban yang disimpan atau ditimpa."""
    # Act / Assert
    assert tracker.updated(_percobaan(filled_at=None), {"answer_text": Change(None, "x")}) == []


def test_isian_yang_dikosongkan_tidak_dicatat(tracker):
    """Edge: filled_at kembali NULL tidak terjadi (trigger SCRUM-231); kalaupun ada, bukan simpan."""
    # Act / Assert
    assert tracker.updated(_percobaan(filled_at=None), {"filled_at": Change(HARI_2, None)}) == []


# Jalur penuh lewat PUT SCRUM-231: listener ORM menulis baris audit di transaksi simpan.


@pytest.fixture
def manual(siap_manual):
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    percobaan = siap_manual.db.scalars(
        select(Attempt).where(Attempt.session_id == sesi_id).order_by(Attempt.id)
    ).all()
    return siap_manual, sesi_id, percobaan


def _simpan(client, sesi_id, attempt, **ubah):
    badan = {
        "snapshot_item_id": str(attempt.snapshot_item_id),
        "attempt_no": attempt.repetition_no,
        "answer_md": JAWABAN_RAHASIA,
        "platform_refused": False,
        "confirm": False,
    }
    badan.update(ubah)
    return client.put(f"/manual-measurements/{sesi_id}/answers", json=badan)


def _catatan(db, sesi_id):
    return db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_id == sesi_id, AuditLog.action.like("measurement_session.answer_%"))
        .order_by(AuditLog.id)
    ).all()


def test_simpan_lalu_timpa_jawaban_tercatat_dengan_pelakunya(manual):
    """Positif: AC9, simpan dan timpa lewat HTTP menghasilkan dua catatan audit berpelaku."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.CONTRIBUTOR)

    # Act
    pertama = _simpan(client, sesi_id, percobaan[0])
    kedua = _simpan(
        client,
        sesi_id,
        percobaan[0],
        answer_md=None,
        platform_refused=True,
        note="Platform menolak",
        confirm=True,
    )

    # Assert
    assert (pertama.status_code, kedua.status_code) == (200, 200), kedua.text
    simpan, timpa = _catatan(ctx.db, sesi_id)
    assert (simpan.action, timpa.action) == (
        "measurement_session.answer_saved",
        "measurement_session.answer_overwritten",
    )
    assert simpan.actor_user_id is not None
    assert (simpan.after["source"], timpa.after["platform_refused"]) == ("manual", True)
    assert "rahasia" not in repr([simpan.after, timpa.after, timpa.before])


def test_timpa_tanpa_konfirmasi_tidak_menulis_audit(manual):
    """Negatif: AC8, 409 tanpa konfirmasi membatalkan semuanya, termasuk catatan audit."""
    # Arrange
    ctx, sesi_id, percobaan = manual
    client = ctx.sebagai(Role.ADMIN)
    _simpan(client, sesi_id, percobaan[0])

    # Act
    respons = _simpan(client, sesi_id, percobaan[0], answer_md="jawaban lain")

    # Assert
    assert respons.status_code == 409
    assert len(_catatan(ctx.db, sesi_id)) == 1


def test_sesi_manual_berlabel_manual_di_daftar_sesi(manual):
    """Positif: AC9, label manual terbawa ke daftar sesi (GET /measurements)."""
    # Arrange
    ctx, sesi_id, _ = manual

    # Act
    respons = ctx.sebagai(Role.ADMIN).get("/measurements")

    # Assert
    assert respons.status_code == 200, respons.text
    (baris,) = [s for s in respons.json() if s["id"] == str(sesi_id)]
    assert baris["session_type"] == "manual"
