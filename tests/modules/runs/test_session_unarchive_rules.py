"""Kapan sesi pengukuran boleh dikembalikan dari arsip. PBI-11 AC10, SCRUM-263.

Unarchive hanya membalik arsip: sesi harus memang sedang diarsipkan. Statusnya
tidak dicek, karena arsip hanya menerima sesi yang sudah berakhir.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

from datetime import UTC, datetime

import pytest

from app.modules.runs.session_rules import ensure_unarchivable
from app.shared.exceptions import ConflictError


def test_sesi_yang_diarsipkan_boleh_dikembalikan():
    """Positif: sesi dengan archived_at terisi lolos."""
    # Act / Assert
    ensure_unarchivable(archived_at=datetime(2026, 11, 1, tzinfo=UTC))


def test_sesi_yang_belum_diarsipkan_ditolak():
    """Negatif: unarchive pada sesi di daftar utama ditolak dengan 409."""
    # Act / Assert
    with pytest.raises(ConflictError) as error:
        ensure_unarchivable(archived_at=None)
    assert error.value.code == "SESSION_NOT_ARCHIVED"


def test_waktu_arsip_epoch_tetap_dianggap_diarsipkan():
    """Edge: hanya None yang berarti belum diarsipkan, bukan nilai falsy lain."""
    # Act / Assert
    ensure_unarchivable(archived_at=datetime.fromtimestamp(0, tz=UTC))
