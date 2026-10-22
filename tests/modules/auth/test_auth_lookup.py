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


@pytest.fixture
def dua_rina(db_session) -> tuple[User, User]:
    """Dua orang berbeda dengan nama persis sama, hanya email yang membedakan."""
    pertama = User(email="rina.a@veritask.ai", name="Rina Sari", role=Role.AUTHOR)
    kedua = User(email="rina.b@veritask.ai", name="Rina Sari", role=Role.REVIEWER, is_active=False)
    db_session.add_all([pertama, kedua])
    db_session.commit()
    return pertama, kedua


def test_identitas_memuat_nama_dan_email(db_session, pengguna):
    # Act
    hasil = service.user_identities(db_session, {pengguna["budi"].id})

    # Assert
    identitas = hasil[pengguna["budi"].id]
    assert (identitas.name, identitas.email) == ("Budi Santoso", "budi@veritask.ai")


def test_nama_sama_tetap_dibedakan_lewat_email(db_session, dua_rina):
    # Arrange
    pertama, kedua = dua_rina

    # Act
    hasil = service.user_identities(db_session, {pertama.id, kedua.id})

    # Assert
    assert hasil[pertama.id].name == hasil[kedua.id].name == "Rina Sari"
    assert hasil[pertama.id].email == "rina.a@veritask.ai"
    assert hasil[kedua.id].email == "rina.b@veritask.ai"


def test_identitas_pengguna_nonaktif_tetap_ada(db_session, pengguna):
    """PBI-1 AC5: pelaku yang sudah dinonaktifkan tetap tampil dengan emailnya."""
    # Act
    hasil = service.user_identities(db_session, {pengguna["lama"].id})

    # Assert
    assert hasil[pengguna["lama"].id].email == "lama@veritask.ai"


def test_identitas_id_tidak_dikenal_dilewati(db_session, pengguna):
    # Arrange
    tidak_dikenal = uuid.uuid4()

    # Act
    hasil = service.user_identities(db_session, {pengguna["rina"].id, tidak_dikenal})

    # Assert
    assert set(hasil) == {pengguna["rina"].id}


def test_identitas_himpunan_kosong_tidak_query(db_session):
    # Act + Assert
    assert service.user_identities(db_session, set()) == {}


def test_reviewer_aktif_untuk_penugasan(db_session, pengguna):
    """PBI-6 AC2: hanya reviewer aktif yang boleh ditugaskan."""
    # Arrange
    sari = User(email="sari@veritask.ai", name="Sari Reviewer", role=Role.REVIEWER)
    nonaktif = User(
        email="tono@veritask.ai", name="Tono Reviewer", role=Role.REVIEWER, is_active=False
    )
    admin = User(email="admin@veritask.ai", name="Admin", role=Role.ADMIN)
    db_session.add_all([sari, nonaktif, admin])
    db_session.commit()

    # Act
    hasil = service.active_reviewer_ids(db_session)

    # Assert
    assert set(hasil) == {pengguna["budi"].id, sari.id}


def test_tanpa_reviewer_aktif(db_session):
    # Act + Assert
    assert service.active_reviewer_ids(db_session) == []
