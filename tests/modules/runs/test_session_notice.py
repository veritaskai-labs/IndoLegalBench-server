"""Notifikasi saat sesi pengukuran berakhir. PBI-11 AC10, SCRUM-210, D2 Tabel 7.

Aturan:
- Hanya di dalam aplikasi, satu penerima: orang yang memulai sesi.
- Jenisnya sesi selesai (measurement_completed) atau sesi gagal (measurement_failed),
  dengan tautan /runs/{id}.
- Tidak ada notifikasi anggaran, peringatan dini, atau ambang kegagalan.

Asumsi menunggu D4 / SCRUM-240: finished_with_failures dikirim sebagai sesi selesai
dengan isi yang menyebut kegagalan teknis. Status yang belum diputuskan tidak
mengirim apa pun.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: kejadian yang tidak boleh mengirim notifikasi.
- Edge: batas atau kasus pinggir.
"""

import uuid

import pytest

from app.modules.notifications.types import MEASUREMENT_COMPLETED, MEASUREMENT_FAILED
from app.modules.runs.session_notice import notify_session_ended

SESI = uuid.UUID("00000000-0000-0000-0000-000000000210")
PEMULAI = uuid.UUID("00000000-0000-0000-0000-0000000000ad")


class _Perekam:
    """Notifier palsu yang mencatat setiap panggilan."""

    def __init__(self) -> None:
        self.panggilan: list[tuple[list[uuid.UUID], str, dict[str, str]]] = []

    def notify(self, user_ids, type, payload) -> None:
        self.panggilan.append((list(user_ids), type, dict(payload)))


def _kirim(status: str) -> _Perekam:
    perekam = _Perekam()
    notify_session_ended(
        perekam, session_id=SESI, label="Run Oktober", status=status, started_by=PEMULAI
    )
    return perekam


def test_sesi_selesai_dikirim_ke_pemulai_saja():
    """Positif: satu notifikasi measurement_completed ke pemulai, tautan ke sesinya."""
    # Act
    perekam = _kirim("finished")

    # Assert
    assert len(perekam.panggilan) == 1
    user_ids, tipe, payload = perekam.panggilan[0]
    assert user_ids == [PEMULAI]
    assert tipe == MEASUREMENT_COMPLETED
    assert payload["link"] == f"/runs/{SESI}"
    assert "Run Oktober" in payload["body"]
    assert payload["title"].strip()


def test_sesi_gagal_dikirim_sebagai_measurement_failed():
    """Positif: sesi gagal memakai tipe measurement_failed."""
    # Act
    perekam = _kirim("failed")

    # Assert
    user_ids, tipe, payload = perekam.panggilan[0]
    assert user_ids == [PEMULAI]
    assert tipe == MEASUREMENT_FAILED
    assert payload["link"] == f"/runs/{SESI}"


def test_selesai_dengan_kegagalan_tetap_sesi_selesai_dan_menyebut_kegagalan():
    """Edge: asumsi D4, sesi tetap selesai tetapi isinya menyebut kegagalan teknis."""
    # Act
    perekam = _kirim("finished_with_failures")

    # Assert
    _, tipe, payload = perekam.panggilan[0]
    assert tipe == MEASUREMENT_COMPLETED
    assert "gagal teknis" in payload["body"]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_sesi_yang_belum_berakhir_tidak_mengirim_apa_pun(status):
    """Negatif: notifikasi hanya saat sesi berakhir."""
    # Act / Assert
    assert _kirim(status).panggilan == []


@pytest.mark.parametrize("status", ["budget_stopped", "cancelled"])
def test_status_yang_belum_diputuskan_tidak_mengirim_apa_pun(status):
    """Edge: tidak ada notifikasi anggaran habis (PBI-12 AC10); status lain menunggu D4."""
    # Act / Assert
    assert _kirim(status).panggilan == []
