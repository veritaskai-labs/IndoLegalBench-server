"""Penyimpanan sesi sementara di memori. SCRUM-76 SCRUM-207.

Tabel measurement_sessions milik SCRUM-205 belum ada. Sampai tabel itu masuk,
InMemorySessionQueue memenuhi port SessionQueueStore supaya POST /measurements
bisa dicoba. Yang diuji di sini perilaku yang nanti juga harus dipenuhi
adapter tabel: sesi baru selalu Menunggu (AC3) dan queue_order naik terus,
sehingga antrean FIFO sesuai D2 ("sisanya queued, urut queued_at").

Label: Positif = perilaku normal, Edge = batas perilaku.
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
