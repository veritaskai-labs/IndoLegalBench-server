"""QA skala pengukuran: 10 kasus x 3 pengulangan = 30 panggilan. SCRUM-76 SCRUM-213.

Satu angka yang sama harus muncul di tiga tempat: ringkasan sebelum Mulai
(AC5), baris percobaan yang dibekukan saat sesi berjalan (AC3, AC6), dan
jumlah panggilan worker ke provider (AC3, AC4). Tes lain hanya memakai 2 atau
3 kasus, jadi di sini skala sesungguhnya dari tiket.

Worker diuji dengan MockProviderAdapter dan penyimpanan palsu. Penyimpanan
sesi di atas tabel belum ada di staging (lihat ports.py), jadi alur penuh
lewat database belum bisa diuji.

Label: Positif = alur normal, Edge = batas perilaku.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.modules.providers.adapters.mock_provider import MockProviderAdapter
from app.modules.runs.dependencies import build_session_queue
from app.modules.runs.models import Attempt, CaseSnapshot, CaseSnapshotItem
from app.modules.runs.worker import run_session
from app.shared.security import Role
from tests.modules.cases.test_cases import _buat, _setujui
from tests.modules.runs.test_start_endpoint import URL as URL_MULAI
from tests.modules.runs.test_start_endpoint import _badan
from tests.modules.runs.test_summary_endpoint import URL as URL_RINGKASAN
from tests.modules.runs.test_worker import FakeSessionStore

KASUS = 10
ULANG = 3


@pytest.fixture
def sepuluh(siap):
    """Suite siap (3 kasus approved) ditambah 7 kasus approved: total 10, dev dan test."""
    for nomor in range(4, KASUS + 1):
        tag = "test" if nomor % 3 == 0 else "dev"
        isi = {
            "title": f"Kasus {nomor}",
            "question": f"Pertanyaan nomor {nomor}?",
            "category": "ketenagakerjaan",
        }
        case_id = _buat(
            siap.client, siap.suite_id, case_code=f"PHK-{nomor:03d}", split_tag=tag, identity=isi
        ).json()["id"]
        _setujui(siap.db, case_id)
    return siap


def test_ringkasan_menghitung_tiga_puluh_panggilan_untuk_sepuluh_kasus(sepuluh):
    """Positif: AC5, jumlah panggilan di ringkasan = kasus approved x pengulangan."""
    # Arrange
    client = sepuluh.sebagai(Role.ADMIN)
    badan = {k: v for k, v in _badan(sepuluh).items() if k not in ("label", "purpose")}

    # Act
    response = client.post(URL_RINGKASAN, json=badan)

    # Assert
    assert response.status_code == 200
    assert response.json()["case_count"] == KASUS
    assert response.json()["total_calls"] == KASUS * ULANG


def test_sesi_berjalan_membekukan_sepuluh_kasus_dengan_tiga_percobaan_masing_masing(sepuluh):
    """Positif: AC3 dan AC6, baris percobaan yang dibekukan sama dengan angka ringkasan."""
    # Arrange
    client = sepuluh.sebagai(Role.ADMIN)
    sesi_id = uuid.UUID(client.post(URL_MULAI, json=_badan(sepuluh)).json()["session_id"])

    # Act
    build_session_queue(sepuluh.db).take_next()

    # Assert
    item = (
        select(func.count())
        .select_from(CaseSnapshotItem)
        .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
        .where(CaseSnapshot.session_id == sesi_id)
    )
    assert sepuluh.db.scalar(item) == KASUS
    per_item = sepuluh.db.execute(
        select(Attempt.snapshot_item_id, func.count())
        .where(Attempt.session_id == sesi_id)
        .group_by(Attempt.snapshot_item_id)
    ).all()
    assert len(per_item) == KASUS
    assert {jumlah for _, jumlah in per_item} == {ULANG}


def test_worker_memanggil_provider_tiga_puluh_kali_dengan_teks_pertanyaan_saja():
    """Positif: AC3 dan AC4, 10 kasus x 3 = 30 panggilan, isi panggilan = pertanyaan apa adanya."""
    # Arrange
    pertanyaan = [f"Pertanyaan nomor {nomor}?" for nomor in range(1, KASUS + 1)]
    store = FakeSessionStore(pertanyaan)

    # Act
    run_session(uuid.uuid4(), store, MockProviderAdapter("success"), repeats=ULANG)

    # Assert
    assert len(store.attempts) == KASUS * ULANG
    assert [rekam.prompt for rekam in store.attempts] == [
        p for p in pertanyaan for _ in range(ULANG)
    ]
    assert all(rekam.text == f"mock:{rekam.prompt}" for rekam in store.attempts)
    assert store.progress[-1] == (KASUS * ULANG, 0)
    # SCRUM-214: running ditulis SessionQueue.start(), worker hanya menulis status akhir.
    assert store.statuses == ["finished"]


def test_semua_panggilan_gagal_tetap_tiga_puluh_percobaan_dan_loop_berlanjut():
    """Edge: pengukuran lanjut walau setiap panggilan gagal; semuanya tercatat gagal.

    Status akhir sengaja tidak dicek. D4 menyebut semua gagal = Gagal dan sebagian gagal =
    Selesai dengan kegagalan, tetapi worker masih menulis "finished" untuk keduanya. Itu celah
    produk yang menunggu keputusan D4 (SCRUM-240), bukan perilaku yang boleh dikunci tes.
    """
    # Arrange
    pertanyaan = [f"Pertanyaan nomor {nomor}?" for nomor in range(1, KASUS + 1)]
    store = FakeSessionStore(pertanyaan)

    # Act
    run_session(uuid.uuid4(), store, MockProviderAdapter("blocked"), repeats=ULANG)

    # Assert
    assert len(store.attempts) == KASUS * ULANG
    assert all(rekam.failure_kind is not None for rekam in store.attempts)
    assert store.progress[-1] == (0, KASUS * ULANG)
    # SCRUM-214: worker hanya menulis satu status akhir (running sudah ditulis start()).
    assert len(store.statuses) == 1
