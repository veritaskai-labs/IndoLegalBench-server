"""Izin tiap peran diuji terhadap matriks yang sudah disepakati tim.

PBI-1 (SCRUM-68) dan PBI-2 (SCRUM-69), sub task QA (SCRUM-96).

Sumbernya satu: docs/pbi1/roles-matrix.svg, hasil SCRUM-97 yang sudah
merge ke staging 25 September. Tabel DIIZINKAN di bawah adalah salinan
matriks itu dalam bentuk yang bisa dijalankan.

Kenapa berkas ini ada padahal tiap modul sudah punya test perannya
sendiri: test yang ada ditulis dari sudut pandang modulnya, jadi yang
diuji adalah peran yang kebetulan terpikir saat itu. Akibatnya yang
terperiksa selalu peran yang ditolak pada aksi tulis, sementara peran
yang seharusnya BOLEH membaca tidak pernah dicoba siapa pun. Di sini
arahnya dibalik: setiap kombinasi peran kali operasi diperiksa, diambil
dari dokumen, bukan dari kode. Kalau kode dan dokumen berbeda, yang
merah adalah kodenya.

Tidak ada data yang perlu disiapkan. Otorisasi diperiksa sebelum
keberadaan data, jadi UUID yang tidak ada pun cukup: peran yang ditolak
mendapat 403, peran yang diizinkan mendapat 404. Yang dibandingkan hanya
"ditolak atau tidak", bukan berhasil atau tidak.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.shared.security import Role

SEMUA_PERAN = list(Role)

# UUID yang sengaja tidak ada. Dipakai supaya operasi pada satu baris
# tertentu bisa diuji izinnya tanpa membuat baris itu lebih dulu.
TIDAK_ADA = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


@dataclass(frozen=True)
class Operasi:
    """Satu sel pada matriks peran, dalam bentuk permintaan HTTP."""

    nama: str
    metode: str
    jalur: str
    boleh: frozenset[Role]
    body: dict[str, Any] | None = field(default=None)


A, R, AD, V = Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER

# Baris "User" pada matriks. Read bertanda "(sendiri)" untuk Author,
# Reviewer, dan Viewer, yang di API ini berarti /me. Admin membaca semua
# lewat /admin/users. Delete kosong untuk keempat peran: akun tidak
# pernah dihapus, hanya dinonaktifkan, supaya riwayat kontribusinya utuh.
OPERASI_PENGGUNA = [
    Operasi("me-baca-diri-sendiri", "GET", "/me", frozenset({A, R, AD, V})),
    Operasi("admin-users-daftar", "GET", "/admin/users", frozenset({AD})),
    Operasi(
        "admin-users-tambah",
        "POST",
        "/admin/users",
        frozenset({AD}),
        {"email": "anggota.baru@veritask.ai", "name": "Anggota Baru", "role": "author"},
    ),
    Operasi(
        "admin-users-ubah-peran",
        "PATCH",
        f"/admin/users/{TIDAK_ADA}",
        frozenset({AD}),
        {"role": "reviewer"},
    ),
    Operasi(
        "admin-users-nonaktifkan",
        "POST",
        f"/admin/users/{TIDAK_ADA}/deactivate",
        frozenset({AD}),
    ),
]

# Baris "Suite" pada matriks. Perhatikan Read: Author, Reviewer, dan
# Admin, tanpa Viewer. Catatan kaki matriks menyatakan arsip termasuk
# Update, jadi archive dan unarchive mengikuti izin Update.
OPERASI_SUITE = [
    Operasi(
        "suite-buat",
        "POST",
        "/suites",
        frozenset({A, AD}),
        {"name": "Suite Uji Matriks", "description": None},
    ),
    Operasi("suite-daftar", "GET", "/suites", frozenset({A, R, AD})),
    Operasi("suite-detail", "GET", f"/suites/{TIDAK_ADA}", frozenset({A, R, AD})),
    Operasi(
        "suite-ubah",
        "PATCH",
        f"/suites/{TIDAK_ADA}",
        frozenset({A, AD}),
        {"description": "diubah"},
    ),
    Operasi("suite-hapus", "DELETE", f"/suites/{TIDAK_ADA}", frozenset({A, AD})),
    Operasi("suite-arsipkan", "POST", f"/suites/{TIDAK_ADA}/archive", frozenset({A, AD})),
    Operasi("suite-aktifkan", "POST", f"/suites/{TIDAK_ADA}/unarchive", frozenset({A, AD})),
]

SEMUA_OPERASI = OPERASI_PENGGUNA + OPERASI_SUITE

KOMBINASI = [(operasi, peran) for operasi in SEMUA_OPERASI for peran in SEMUA_PERAN]
NAMA_KOMBINASI = [f"{operasi.nama}--{peran.value}" for operasi, peran in KOMBINASI]


def _panggil(client, operasi: Operasi):
    kirim = getattr(client, operasi.metode.lower())
    if operasi.body is None:
        return kirim(operasi.jalur)
    return kirim(operasi.jalur, json=operasi.body)


@pytest.mark.parametrize("operasi,peran", KOMBINASI, ids=NAMA_KOMBINASI)
def test_izin_sesuai_matriks_peran(as_role, operasi: Operasi, peran: Role):
    """Setiap sel matriks diperiksa: yang boleh tidak ditolak, yang tidak boleh ditolak 403."""
    client = as_role(peran)

    respons = _panggil(client, operasi)

    if peran in operasi.boleh:
        assert respons.status_code != 403, (
            f"{peran.value} seharusnya boleh {operasi.nama} menurut "
            f"docs/pbi1/roles-matrix.svg, tetapi ditolak 403. "
            f"Kode dan matriks yang disepakati berbeda; salah satunya harus diperbaiki."
        )
    else:
        assert respons.status_code == 403, (
            f"{peran.value} seharusnya ditolak untuk {operasi.nama} menurut "
            f"docs/pbi1/roles-matrix.svg, tetapi jawabannya {respons.status_code}."
        )


def test_matriks_yang_diuji_memang_menyebut_keempat_peran():
    """Penjaga agar tabel di atas tidak diam-diam kehilangan satu peran.

    Kalau enum Role bertambah, kombinasi ikut bertambah otomatis, dan
    setiap operasi harus ditinjau ulang terhadap matriks. Test ini yang
    memaksa peninjauan itu terjadi.
    """
    assert len(SEMUA_PERAN) == 4, (
        "Jumlah peran berubah. Tinjau ulang docs/pbi1/roles-matrix.svg "
        "dan tabel operasi di berkas ini sebelum menyesuaikan angka ini."
    )
    for operasi in SEMUA_OPERASI:
        assert operasi.boleh, f"{operasi.nama} tidak mengizinkan peran mana pun"
        assert operasi.boleh <= set(SEMUA_PERAN), f"{operasi.nama} menyebut peran di luar enum"
