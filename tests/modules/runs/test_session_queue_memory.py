"""Penyimpanan sesi sementara di memori. SCRUM-76 SCRUM-207.

Tabel measurement_sessions milik SCRUM-205 belum ada. Sampai tabel itu masuk,
InMemorySessionQueue memenuhi port SessionQueueStore supaya POST /measurements
bisa dicoba. Yang diuji di sini perilaku yang nanti juga harus dipenuhi
adapter tabel: sesi baru selalu Menunggu (AC3) dan queue_order naik terus,
sehingga antrean FIFO sesuai D2 ("sisanya queued, urut queued_at"). Saat
giliran tiba, start mengubah status dan menyimpan snapshot serta salinan
produk sekaligus (AC2, AC6), dan menolak bila sesi lain sudah berjalan,
seperti partial unique index "satu sesi automatic running" di D2.

Label: Positif = perilaku normal, Negatif = ditolak, Edge = batas perilaku.
"""

import threading
import uuid
from datetime import UTC, datetime

from app.modules.runs import adapters, schemas, session_ports

PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
JAM = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _sesi(label: str = "Run Oktober") -> session_ports.NewSession:
    return session_ports.NewSession(
        label=label,
        purpose=schemas.MeasurementPurpose.OFFICIAL,
        product_id=PRODUCT_ID,
        suite_ids=(SUITE_A,),
        case_filter=schemas.CaseFilter.BOTH,
        repetitions=3,
        created_by=ADMIN_ID,
    )


def test_sesi_baru_menunggu_dengan_urutan_pertama():
    """Positif: sesi pertama Menunggu dengan queue_order 1 (AC3)."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)

    # Act
    sesi = antrean.create_queued(_sesi())

    # Assert
    assert sesi.status == schemas.SessionStatus.QUEUED
    assert sesi.queue_order == 1
    assert isinstance(sesi.id, uuid.UUID)


def test_urutan_antrean_naik_dan_id_berbeda():
    """Positif: sesi berikutnya mendapat urutan lebih besar, jadi yang lebih dulu jalan lebih dulu."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)

    # Act
    pertama = antrean.create_queued(_sesi("A"))
    kedua = antrean.create_queued(_sesi("B"))

    # Assert
    assert (pertama.queue_order, kedua.queue_order) == (1, 2)
    assert pertama.id != kedua.id


def test_isian_dan_waktu_masuk_antrean_tersimpan():
    """Positif: pilihan pemulai dan queued_at tersimpan untuk giliran antrean (D3 langkah 21)."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)

    # Act
    sesi = antrean.create_queued(_sesi())

    # Assert
    tersimpan = antrean.get(sesi.id)
    assert tersimpan.new == _sesi()
    assert tersimpan.queued_at == JAM
    assert tersimpan.status == schemas.SessionStatus.QUEUED


def test_sesi_yang_tidak_ada_dijawab_none():
    """Edge: id yang tidak pernah dibuat tidak melempar galat."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)

    # Act + Assert
    assert antrean.get(uuid.uuid4()) is None


def test_pembuatan_bersamaan_tidak_membuat_urutan_ganda():
    """Edge: dua Admin menekan Mulai bersamaan, urutan antrean tetap unik."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    hasil: list[int] = []

    def buat():
        hasil.append(antrean.create_queued(_sesi()).queue_order)

    # Act
    pekerja = [threading.Thread(target=buat) for _ in range(20)]
    for satu in pekerja:
        satu.start()
    for satu in pekerja:
        satu.join()

    # Assert
    assert sorted(hasil) == list(range(1, 21))


ITEM = session_ports.SnapshotItem(
    case_id=uuid.UUID("00000000-0000-0000-0000-0000000000f1"),
    case_version_id=uuid.UUID("00000000-0000-0000-0000-0000000000f2"),
    split_tag="dev",
    content={"sections": {"case_code": "PHK-001"}},
)
SALINAN = session_ports.ProductCopy(
    product_id=PRODUCT_ID,
    name="AiYU",
    measurement_type="automatic",
    provider_type="openai_compatible",
    base_url="https://api.example.test/v1/chat/completions",
    model_name="deepseek-flash",
    model_params=None,
    rate_limit_per_minute=30,
    price_input_usd_per_mtok=None,
    price_output_usd_per_mtok=None,
    monthly_budget_idr=None,
)
MULAI = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)


def test_menunggu_tertua_adalah_yang_pertama_masuk():
    """Positif: FIFO menurut queue_order (D2: sisanya queued, urut queued_at)."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    pertama = antrean.create_queued(_sesi("A"))
    antrean.create_queued(_sesi("B"))

    # Act
    tertua = antrean.oldest_queued()

    # Assert
    assert tertua.id == pertama.id
    assert tertua.new.label == "A"


def test_antrean_kosong_tidak_punya_sesi_tertua_maupun_sesi_berjalan():
    """Edge: penyimpanan baru tidak punya sesi apa pun."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)

    # Act + Assert
    assert antrean.oldest_queued() is None
    assert antrean.running_exists() is False


def test_start_menjalankan_sesi_dan_menyimpan_snapshot_serta_salinan_produk():
    """Positif: AC2, AC6, status, snapshot, salinan produk, dan started_at berubah bersamaan."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    sesi = antrean.create_queued(_sesi())

    # Act
    berhasil = antrean.start(sesi.id, items=[ITEM], product=SALINAN, started_at=MULAI)

    # Assert
    assert berhasil is True
    tersimpan = antrean.get(sesi.id)
    assert tersimpan.status == schemas.SessionStatus.RUNNING
    assert tersimpan.snapshot == (ITEM,)
    assert tersimpan.product_copy == SALINAN
    assert tersimpan.started_at == MULAI
    assert antrean.running_exists() is True


def test_sesi_yang_berjalan_keluar_dari_antrean_menunggu():
    """Positif: setelah sesi pertama berjalan, sesi kedua menjadi yang tertua."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    pertama = antrean.create_queued(_sesi("A"))
    kedua = antrean.create_queued(_sesi("B"))

    # Act
    antrean.start(pertama.id, items=[ITEM], product=SALINAN, started_at=MULAI)

    # Assert
    assert antrean.oldest_queued().id == kedua.id


def test_start_ditolak_saat_sesi_lain_sudah_berjalan():
    """Negatif: AC3, perilaku partial unique index "satu sesi automatic running" (D2)."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    pertama = antrean.create_queued(_sesi("A"))
    kedua = antrean.create_queued(_sesi("B"))
    antrean.start(pertama.id, items=[ITEM], product=SALINAN, started_at=MULAI)

    # Act
    berhasil = antrean.start(kedua.id, items=[ITEM], product=SALINAN, started_at=MULAI)

    # Assert
    assert berhasil is False
    assert antrean.get(kedua.id).status == schemas.SessionStatus.QUEUED
    assert antrean.get(kedua.id).snapshot == ()


def test_start_sesi_yang_tidak_menunggu_ditolak():
    """Edge: id yang tidak ada, atau sesi yang sudah berjalan, tidak bisa dimulai lagi."""
    # Arrange
    antrean = adapters.InMemorySessionQueue(clock=lambda: JAM)
    sesi = antrean.create_queued(_sesi())
    antrean.start(sesi.id, items=[ITEM], product=SALINAN, started_at=MULAI)

    # Act + Assert
    assert antrean.start(sesi.id, items=[], product=SALINAN, started_at=MULAI) is False
    assert antrean.start(uuid.uuid4(), items=[], product=SALINAN, started_at=MULAI) is False
    assert antrean.get(sesi.id).snapshot == (ITEM,)
