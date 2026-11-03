"""Snapshot manual dari halaman Suite dipensiunkan. SCRUM-76 SCRUM-207.

PBI-11 AC7: tombol 'Buat snapshot' dan daftar snapshot dihapus dari halaman
Suite. D2: "Tidak ada tombol 'Buat snapshot' di halaman Suite; daftar snapshot
pindah ke halaman Eksekusi. Tabel suite_snapshots Sprint 2 dipensiunkan".
Snapshot sekarang dibuat otomatis saat sesi pengukuran berjalan, dan detailnya
dibuka dari halaman Eksekusi, jadi ketiga rute SCRUM-137/138 ikut dihapus.

Label: Negatif = rute lama tidak bisa dipakai lagi.
"""

import uuid

import pytest

from app.shared.security import Role
from tests.modules.cases.test_cases import _suite

RUTE_LAMA = [
    ("POST", "/suites/{suite_id}/snapshots"),
    ("GET", "/suites/{suite_id}/snapshots"),
    ("GET", "/snapshots/{snapshot_id}"),
]


@pytest.mark.parametrize(("metode", "path"), RUTE_LAMA)
def test_rute_snapshot_manual_tidak_ada_lagi_bahkan_untuk_admin(as_role, metode, path):
    """Negatif: Admin pun tidak bisa membuat, mendaftar, atau membuka snapshot manual (AC7)."""
    # Arrange
    client = as_role(Role.ADMIN)
    suite_id = _suite(client)
    alamat = path.format(suite_id=suite_id, snapshot_id=uuid.uuid4())

    # Act
    response = client.request(metode, alamat)

    # Assert
    assert response.status_code == 404


def test_kontrak_openapi_tidak_memuat_snapshot_manual(client):
    """Negatif: FE tidak lagi mendapat rute dan schema snapshot manual dari kontrak."""
    # Arrange + Act
    spec = client.get("/openapi.json").json()

    # Assert
    assert not any("snapshots" in path for path in spec["paths"])
    for schema in ("SnapshotRead", "SnapshotItemRead", "SnapshotSummary", "Page_SnapshotSummary_"):
        assert schema not in spec["components"]["schemas"]
