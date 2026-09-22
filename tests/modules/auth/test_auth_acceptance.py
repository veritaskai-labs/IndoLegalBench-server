"""Test penerimaan PBI-1 (SCRUM-68), sub task [QA] SCRUM-96.

File ini hanya berisi yang BELUM diuji developer, supaya tidak ada
test ganda. Sisanya sudah diuji di PR pemiliknya:

- AC-1 login, cookie sesi, /me, logout      PR #7 (SCRUM-90)
- AC-3 idle timeout dan batas absolut sesi  PR #8 (SCRUM-91)
- AC-4 tambah, ubah peran, nonaktifkan      PR #3 (SCRUM-92)
- AC-5 akun nonaktif ditolak login          PR #7
- AC-6 kontrak tanpa field password         PR #7
- AC-7 dialog konfirmasi                    UI, masuk skenario UAT

xfail di sini sengaja TIDAK strict. Kalau strict, PR teman yang
mengaktifkan fiturnya akan ikut merah karena marker milik QA. Cukup
QA yang memantau baris "xpassed" di ringkasan pytest lalu melepas
markernya.
"""

import pytest

import app.modules.auth.models  # noqa: F401  (mendaftarkan tabel auth ke Base.metadata)
from app.shared.database import Base
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role


def test_ac2_author_boleh_membuat_suite(as_role):
    # Belum bukti AC-2: suites belum memakai require_roles, jadi test ini
    # juga lolos sebagai VIEWER. Gunanya menjaga Author tetap bisa bekerja
    # setelah guard dipasang.
    client = as_role(Role.AUTHOR)

    response = client.post(
        "/suites",
        json={"name": "Ketenagakerjaan QA", "description": "Dipakai test PBI-1"},
    )

    assert response.status_code == 201


@pytest.mark.xfail(
    reason="RED: require_roles belum diaktifkan di router suites (TODO di suites/router.py)",
)
def test_ac2_viewer_ditolak_membuat_suite(as_role):
    client = as_role(Role.VIEWER)

    response = client.post("/suites", json={"name": "Coba Viewer", "description": None})

    assert response.status_code == 403
    assert response.json()["code"] == ForbiddenError.code


@pytest.mark.skip(reason="TODO: butuh modul cases untuk membuktikan kontribusi tetap tercatat")
def test_ac5_kontribusi_akun_nonaktif_tetap_tercatat(as_role):
    """AC-5, bagian yang belum diuji siapa pun.

    Penolakan login akun nonaktif sudah diuji di PR #7. Yang tersisa:
    kasus dan review yang pernah dibuat akun itu tetap tercatat atas
    namanya, bukan hilang atau menjadi anonim. Menunggu modul cases.
    """


def test_ac6_tidak_ada_kolom_kata_sandi_di_database():
    if "users" not in Base.metadata.tables:
        pytest.skip("menunggu tabel users (SCRUM-89, PR #5)")

    for tabel in Base.metadata.tables.values():
        for kolom in tabel.columns:
            nama = kolom.name.lower()
            for terlarang in ("password", "passwd", "sandi"):
                assert terlarang not in nama, f"{tabel.name}.{kolom.name} menyimpan kata sandi"
