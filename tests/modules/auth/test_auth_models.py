"""Test tabel users dan sessions.

PBI-1, sub task "[BE] First DB Schema & Migration".

Test di sini menjaga keputusan skema yang gampang rusak tanpa sadar,
bukan sekadar membuktikan kolomnya ada. Yang dijaga: nilai enum peran
disimpan huruf kecil, email wajib unik, zitadel_sub boleh kosong untuk
banyak baris, dan default kolom tetap jalan untuk insert di luar ORM.

Catatan: test berjalan di SQLite. Perilaku ON DELETE CASCADE tidak ikut
diuji di sini karena SQLite mematikan foreign key secara default, jadi
yang teruji hanya pragma SQLite dan bukan skema kita. Cascade sudah
diverifikasi langsung ke PostgreSQL saat migration dikerjakan.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User, UserSession
from app.shared.security import Role


def buat_user(**override) -> User:
    bawaan = {
        "email": "author@veritask.test",
        "name": "Uji Author",
        "role": Role.AUTHOR,
    }
    return User(**{**bawaan, **override})


def test_user_baru_aktif_dan_timestamp_terisi(db_session):
    user = buat_user()
    db_session.add(user)
    db_session.commit()

    assert user.is_active is True
    assert user.created_at is not None
    assert user.updated_at is not None


def test_zitadel_sub_kosong_saat_akun_dibuat_admin(db_session):
    """Admin membuat akun dari email saja, subject Zitadel belum diketahui."""
    user = buat_user()
    db_session.add(user)
    db_session.commit()

    assert user.zitadel_sub is None


def test_role_disimpan_sebagai_huruf_kecil(db_session):
    """Kontrak API memakai "author", bukan "AUTHOR".

    Tanpa values_callable di kolom role, SQLAlchemy menyimpan nama
    anggota enum dan nilai di database jadi huruf besar.
    """
    db_session.add(buat_user(role=Role.AUTHOR))
    db_session.commit()

    tersimpan = db_session.execute(text("SELECT role FROM users")).scalar_one()
    assert tersimpan == "author"


def test_setiap_peran_bisa_disimpan(db_session):
    for index, role in enumerate(Role):
        db_session.add(buat_user(email=f"peran{index}@veritask.test", role=role))
    db_session.commit()

    tersimpan = db_session.execute(text("SELECT role FROM users ORDER BY email")).scalars().all()
    assert tersimpan == ["author", "reviewer", "admin", "viewer"]


def test_email_duplikat_ditolak(db_session):
    """AC4: email anggota tim tidak boleh bentrok, endpoint membalas 409."""
    db_session.add(buat_user())
    db_session.commit()

    db_session.add(buat_user(name="Orang Lain"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_banyak_user_boleh_sama_sama_belum_punya_zitadel_sub(db_session):
    """Unique di zitadel_sub tidak boleh menghalangi akun yang belum login."""
    db_session.add(buat_user(email="satu@veritask.test"))
    db_session.add(buat_user(email="dua@veritask.test"))
    db_session.commit()

    jumlah = db_session.execute(
        text("SELECT count(*) FROM users WHERE zitadel_sub IS NULL")
    ).scalar_one()
    assert jumlah == 2


def test_zitadel_sub_duplikat_ditolak(db_session):
    """Satu akun Zitadel tidak boleh terpetakan ke dua user platform."""
    db_session.add(buat_user(email="satu@veritask.test", zitadel_sub="sub-123"))
    db_session.commit()

    db_session.add(buat_user(email="dua@veritask.test", zitadel_sub="sub-123"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_default_kolom_jalan_untuk_insert_di_luar_orm(db_session):
    """Seed dan migration menulis baris tanpa lewat ORM.

    Kalau is_active dan timestamp hanya punya default Python, baris
    hasil insert mentah akan kosong atau ditolak.
    """
    db_session.execute(
        text("INSERT INTO users (id, email, name, role) VALUES (:id, :email, :name, :role)"),
        {
            "id": uuid.uuid4().hex,
            "email": "mentah@veritask.test",
            "name": "Insert Mentah",
            "role": Role.ADMIN.value,
        },
    )
    db_session.commit()

    baris = db_session.execute(text("SELECT is_active, created_at, updated_at FROM users")).one()
    assert baris.is_active
    assert baris.created_at is not None
    assert baris.updated_at is not None


def test_sesi_terhubung_ke_user(db_session):
    user = buat_user()
    db_session.add(user)
    db_session.commit()

    sekarang = datetime.now(UTC)
    db_session.add(
        UserSession(
            user_id=user.id,
            last_activity_at=sekarang,
            expires_at=sekarang + timedelta(hours=8),
        )
    )
    db_session.commit()
    db_session.refresh(user)

    assert len(user.sessions) == 1
    assert user.sessions[0].user_id == user.id


def test_sesi_boleh_tanpa_kolom_oidc(db_session):
    """Sesi dari seed, test, dan fake IdP tidak punya sid maupun id_token.

    Keduanya nullable justru supaya alur di luar OIDC tetap bisa
    membuat sesi.
    """
    user = buat_user()
    db_session.add(user)
    db_session.commit()

    sekarang = datetime.now(UTC)
    sesi = UserSession(
        user_id=user.id,
        last_activity_at=sekarang,
        expires_at=sekarang + timedelta(hours=8),
    )
    db_session.add(sesi)
    db_session.commit()

    assert sesi.zitadel_sid is None
    assert sesi.id_token is None


def test_kolom_oidc_tersimpan_saat_login_lewat_zitadel(db_session):
    """SCRUM-90 menyimpan id_token untuk id_token_hint saat end_session."""
    user = buat_user()
    db_session.add(user)
    db_session.commit()

    sekarang = datetime.now(UTC)
    db_session.add(
        UserSession(
            user_id=user.id,
            last_activity_at=sekarang,
            expires_at=sekarang + timedelta(hours=8),
            zitadel_sid="sid-abc123",
            id_token="header.payload.signature",
        )
    )
    db_session.commit()

    baris = db_session.execute(text("SELECT zitadel_sid, id_token FROM sessions")).one()
    assert baris.zitadel_sid == "sid-abc123"
    assert baris.id_token == "header.payload.signature"
