"""Tabel database milik modul auth.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/auth/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.

Dua tabel di sini punya siklus hidup yang berbeda, jangan tertukar:

- Baris users TIDAK PERNAH dihapus, hanya dinonaktifkan lewat is_active.
  PBI-1 AC5 mewajibkan kasus dan review yang pernah dibuat tetap
  tercatat atas nama pembuatnya, termasuk setelah akunnya dinonaktifkan.
- Baris sessions justru dihapus permanen di tiga tempat: logout,
  idle timeout, dan saat sebuah akun dinonaktifkan admin.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, Uuid, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base
from app.shared.security import Role


class User(Base):
    __tablename__ = "users"

    # Uuid generik SQLAlchemy 2.0, jadi tabel yang sama bisa dipakai
    # PostgreSQL di production dan SQLite saat test.
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Kosong sampai pengguna login pertama kali. Admin membuat akun dari
    # email saja, subject Zitadel baru diketahui saat callback OIDC, dan
    # saat itulah kolom ini diisi. Unique tetap dipasang: PostgreSQL
    # mengizinkan banyak baris NULL di kolom unique.
    zitadel_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    # Kunci penghubung ke akun Zitadel selama zitadel_sub masih kosong,
    # jadi wajib unik dan tidak boleh null.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # values_callable wajib ada. Tanpa itu SQLAlchemy menyimpan NAMA
    # anggota enum ("AUTHOR"), bukan nilainya ("author"), sedangkan
    # kontrak API dan seluruh tiket memakai huruf kecil.
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role_enum", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    # default untuk insert lewat ORM, server_default untuk insert lain
    # seperti seed dan migration.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # Biarkan ON DELETE CASCADE di database yang bekerja, jangan
        # tarik seluruh baris sesi ke memori lebih dulu.
        passive_deletes=True,
    )


class UserSession(Base):
    """Sesi login aktif.

    Sengaja TIDAK dinamai Session. Repository dan service memakai
    sqlalchemy.orm.Session untuk session database, dan dua nama yang
    sama di satu file hanya akan memaksa alias di mana-mana.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # index dipakai saat menghapus seluruh sesi milik satu pengguna,
    # yang terjadi tiap kali admin menonaktifkan sebuah akun.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Diperbarui setiap request yang terautentikasi, dipakai menghitung
    # idle timeout.
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Batas waktu absolut, terpisah dari idle timeout.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Dua kolom di bawah diisi alur OIDC (SCRUM-90) dan nullable, karena
    # sesi yang lahir di luar alur itu (seed, test, fake IdP) tidak
    # punya keduanya.

    # Klaim sid dari Zitadel. Belum dibaca siapa pun, disimpan sejak
    # sekarang supaya pemakaiannya nanti tidak menuntut migration kedua.
    zitadel_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Dipakai sebagai id_token_hint saat end_session di Zitadel, supaya
    # logout ikut mengakhiri sesi di sisi IdP, bukan hanya di sini.
    # Text cukup untuk satu JWT. Sengaja TIDAK dienkripsi di sprint ini:
    # token ini hanya mengakhiri sesi IdP, dan enkripsi berarti dekripsi
    # di setiap logout.
    id_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
