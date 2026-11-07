"""Developer AI di sesi berjalan: jawaban dev terbuka, isi kasus test tidak bocor. SCRUM-261.

PBI-11 AC9. Satu sesi berjalan berisi kasus dev dan kasus test. Teks sentinel
ditaruh di semua tempat yang bisa membawa isi kasus test (kode, judul,
pertanyaan, jawaban, teks galat, dan respons mentah penyedia), lalu setiap
endpoint GET pengukuran dibaca sebagai Developer AI.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: isi yang tidak boleh keluar.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import Attempt, AttemptStatus, CallLog, SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_detail_endpoint import _body, _kasus, _snapshot
from tests.modules.runs.test_session_list_endpoint import _sesi

JAM = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)

KODE_TEST = "KASUS-RAHASIA-77"
PERTANYAAN_TEST = "Pertanyaan sentinel kasus test"
JAWABAN_TEST = "Jawaban sentinel kasus test"
GALAT_TEST = "Galat sentinel memuat pertanyaan kasus test"
RAW_TEST = "Respons mentah sentinel kasus test"
SENTINEL_TEST = (
    KODE_TEST,
    PERTANYAAN_TEST,
    JAWABAN_TEST,
    GALAT_TEST,
    RAW_TEST,
    "Judul KASUS-RAHASIA",
)

JAWABAN_DEV = "Jawaban kasus dev yang sudah masuk"


@pytest.fixture
def sesi_berjalan(db_session):
    """Sesi berjalan: kasus dev (satu jawaban masuk) dan kasus test (satu jawaban, satu gagal)."""
    pembuat = User(email="pembuat-live@veritask.test", name="Admin Live", role=Role.ADMIN)
    produk = _otomatis("Produk Live")
    db_session.add_all([pembuat, produk])
    db_session.commit()
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    snapshot = _snapshot(db_session, sesi)
    _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-DEV-1", "dev", "Pertanyaan kasus dev"),
        [(AttemptStatus.SUCCEEDED, JAWABAN_DEV), (AttemptStatus.PENDING, None)],
    )
    item_test = _kasus(
        db_session,
        sesi,
        snapshot,
        _body(KODE_TEST, "test", PERTANYAAN_TEST),
        [(AttemptStatus.SUCCEEDED, JAWABAN_TEST), (AttemptStatus.TECHNICAL_FAILURE, None)],
    )
    percobaan = (
        db_session.query(Attempt)
        .filter(Attempt.snapshot_item_id == item_test.id)
        .order_by(Attempt.repetition_no)
        .all()
    )
    for nomor, attempt in enumerate(percobaan, 1):
        db_session.add(
            CallLog(
                attempt_id=attempt.id,
                product_id=sesi.product_id,
                started_at=JAM + timedelta(minutes=nomor),
                ended_at=JAM + timedelta(minutes=nomor, seconds=5),
                duration_ms=5000,
                usd_idr_rate=Decimal("16000"),
                model_version="model-uji-2026",
                result="ok" if nomor == 1 else "provider_error",
                failure_kind=None if nomor == 1 else "provider_error",
                raw={"message": RAW_TEST, "_execution": {"error": GALAT_TEST}},
                try_no=1,
                is_manual_retry=False,
            )
        )
    db_session.commit()
    return sesi, percobaan


def _semua_get(client, sesi, percobaan: list[Attempt]) -> dict[str, str]:
    """Isi respons setiap endpoint GET pengukuran, per URL."""
    urls = [
        "/measurements",
        f"/measurements/{sesi.id}",
        f"/measurements/{sesi.id}/progress",
        *[f"/measurements/{sesi.id}/attempts/{attempt.id}/calls" for attempt in percobaan],
    ]
    return {url: client.get(url).text for url in urls}


def test_developer_ai_tidak_menerima_isi_kasus_test_dari_endpoint_mana_pun(as_role, sesi_berjalan):
    """Negatif: tidak ada sentinel kasus test di daftar, detail, progres, maupun detail teknis."""
    # Arrange
    sesi, percobaan = sesi_berjalan
    client = as_role(Role.DEVELOPER_AI)

    # Act
    isi = _semua_get(client, sesi, percobaan)

    # Assert
    for url, teks in isi.items():
        for sentinel in SENTINEL_TEST:
            assert sentinel not in teks, f"{sentinel!r} bocor di {url}"


def test_developer_ai_menerima_jawaban_kasus_dev_dari_detail_sesi_berjalan(as_role, sesi_berjalan):
    """Positif: jawaban dev yang sudah masuk terbaca, percobaan yang belum jalan tetap kosong."""
    # Arrange
    sesi, _ = sesi_berjalan

    # Act
    body = as_role(Role.DEVELOPER_AI).get(f"/measurements/{sesi.id}").json()

    # Assert
    assert body["status"] == "running"
    dev = next(kasus for kasus in body["cases"] if not kasus["masked"])
    assert [a["answer_text"] for a in dev["attempts"]] == [JAWABAN_DEV, None]


def test_admin_melihat_sentinel_yang_sama(as_role, sesi_berjalan):
    """Positif: kontrol. Sentinel memang ada di data, jadi tes kebocoran tidak kosong makna."""
    # Arrange
    sesi, percobaan = sesi_berjalan
    client = as_role(Role.ADMIN)

    # Act
    isi = _semua_get(client, sesi, percobaan)

    # Assert
    detail = isi[f"/measurements/{sesi.id}"]
    assert PERTANYAAN_TEST in detail
    assert JAWABAN_TEST in detail
    assert GALAT_TEST in "".join(isi.values())
    assert RAW_TEST in "".join(isi.values())
