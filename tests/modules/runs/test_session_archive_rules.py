"""Kapan sesi pengukuran boleh diarsipkan. PBI-11 AC10, SCRUM-210.

Sesi tidak bisa dihapus, hanya diarsipkan (disembunyikan dari daftar, data tetap
tersimpan). Aturan tambahan:
- Sesi queued atau running ditolak. Arsip tidak mengubah status, jadi sesi running
  yang diarsipkan tetap menahan satu-satunya slot sesi otomatis dan hilang dari
  daftar (blocker 1 di review server #89).
- Sesi yang sudah diarsipkan ditolak, supaya archived_at dan archived_by tidak
  tertimpa. Sesi tidak punya fitur batal arsip.
- Status PBI-12 yang belum diputuskan D4 (misalnya stopped, cancelled) ditolak
  sampai ada keputusan.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

from datetime import UTC, datetime

import pytest

from app.modules.runs.session_rules import ensure_archivable
from app.shared.exceptions import ConflictError


@pytest.mark.parametrize("status", ["finished", "finished_with_failures", "failed"])
def test_sesi_yang_sudah_berakhir_boleh_diarsipkan(status):
    """Positif: sesi yang sudah berakhir boleh diarsipkan."""
    # Act / Assert
    ensure_archivable(status, archived_at=None)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_sesi_yang_antre_atau_berjalan_ditolak(status):
    """Negatif: sesi aktif tidak bisa diarsipkan."""
    # Act / Assert
    with pytest.raises(ConflictError) as error:
        ensure_archivable(status, archived_at=None)
    assert error.value.code == "SESSION_NOT_ARCHIVABLE"


def test_sesi_yang_sudah_diarsipkan_ditolak():
    """Negatif: arsip kedua ditolak walau statusnya sudah berakhir."""
    # Act / Assert
    with pytest.raises(ConflictError) as error:
        ensure_archivable("finished", archived_at=datetime(2026, 11, 1, tzinfo=UTC))
    assert error.value.code == "SESSION_ALREADY_ARCHIVED"


@pytest.mark.parametrize("status", ["stopped", "cancelled"])
def test_status_yang_belum_diputuskan_d4_ditolak(status):
    """Edge: status baru dari PBI-12 tidak otomatis boleh diarsipkan."""
    # Act / Assert
    with pytest.raises(ConflictError) as error:
        ensure_archivable(status, archived_at=None)
    assert error.value.code == "SESSION_NOT_ARCHIVABLE"
