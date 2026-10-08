"""Test pencarian pengguna untuk modul lain (PBI-18 AC3).

Audit log menyaring berdasarkan nama pengguna dan menampilkan nama
pelaku. Modul audit tidak boleh membaca tabel users langsung, jadi
keduanya lewat auth.service.
"""

import uuid

import pytest

from app.modules.auth import service
from app.modules.auth.models import User
from app.shared.security import Role


@pytest.fixture
def pengguna(db_session) -> dict[str, User]:
    baris = {
        "rina": User(email="rina.author@veritask.ai", name="Rina Sari", role=Role.AUTHOR),
        "budi": User(email="budi@veritask.ai", name="Budi Santoso", role=Role.REVIEWER),
        "lama": User(email="lama@veritask.ai", name="Rina Lama", role=Role.AUTHOR, is_active=False),
    }
    db_session.add_all(baris.values())
    db_session.commit()
    return baris


def test_cari_nama_sebagian_tanpa_peduli_kapital(db_session, pengguna):
    # Act
    hasil = service.find_user_ids(db_session, "rina")

    # Assert
    assert set(hasil) == {pengguna["rina"].id, pengguna["lama"].id}


def test_pengguna_nonaktif_tetap_bisa_dicari(db_session, pengguna):
    """PBI-1 AC5: jejak pengguna nonaktif tetap bisa ditelusuri."""
    # Act
    hasil = service.find_user_ids(db_session, "Lama")

    # Assert
    assert hasil == [pengguna["lama"].id]


def test_cari_lewat_email(db_session, pengguna):
    # Act
    hasil = service.find_user_ids(db_session, "BUDI@")

    # Assert
    assert hasil == [pengguna["budi"].id]


def test_tidak_ada_yang_cocok(db_session, pengguna):
    # Act + Assert
    assert service.find_user_ids(db_session, "tidak-ada") == []


def test_karakter_wildcard_dicari_apa_adanya(db_session, pengguna):
    """Edge case: '%' dan '_' bukan wildcard, jadi tidak mencocokkan semua orang."""
    # Act + Assert
    assert service.find_user_ids(db_session, "%") == []
    assert service.find_user_ids(db_session, "_") == []


def test_nama_untuk_sekumpulan_id(db_session, pengguna):
    # Arrange
    tidak_dikenal = uuid.uuid4()

    # Act
    nama = service.user_names(db_session, {pengguna["rina"].id, pengguna["budi"].id, tidak_dikenal})

    # Assert
    assert nama == {pengguna["rina"].id: "Rina Sari", pengguna["budi"].id: "Budi Santoso"}


def test_nama_untuk_himpunan_kosong_tidak_query(db_session):
    # Act + Assert
    assert service.user_names(db_session, set()) == {}
