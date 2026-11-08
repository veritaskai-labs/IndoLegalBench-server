"""Bantuan bersama tes QA sesi pengukuran manual. PBI-22 (SCRUM-176), SCRUM-238.

Tes QA membaca sesi manual lewat HTTP seperti halaman sesi dan kartu jawaban (SCRUM-235/237).
Data dan pengguna disiapkan sekali di sini, di atas fixture siap_manual dari tes SCRUM-230,
supaya tidak ada cara kedua menyiapkan sesi.

siap_manual.sebagai(role) selalu memakai id pengguna yang sama. AC5 dan AC11 butuh orang
yang berbeda (dua pengisi, pembuat sesi lain), jadi `orang` memasang pengguna dengan id dan
nama sendiri.
"""

import uuid

from app.shared.security import CurrentUser, Role
from tests.modules.cases.test_cases import _ganti
from tests.modules.runs.test_manual_session_endpoint import _badan

URL = "/manual-measurements"


def orang(siap, role: Role, nama: str):
    """Pasang pengguna `nama` berperan `role` di client siap_manual. Id-nya tetap per nama."""
    pengguna = CurrentUser(
        user_id=uuid.uuid5(uuid.NAMESPACE_URL, f"qa-manual/{nama}"),
        name=nama,
        email=f"{nama.lower().replace(' ', '.')}@veritask.test",
        role=role,
    )
    _ganti(siap.client, pengguna)
    return siap.client


def id_orang(nama: str) -> str:
    """Id pengguna `orang(…, nama)` dalam bentuk teks, seperti di respons API."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qa-manual/{nama}"))


def buat_sesi(siap, *, oleh: str = "Admin Satu", **ubah) -> dict:
    """POST sesi manual sebagai Admin `oleh`, lalu kembalikan detailnya."""
    client = orang(siap, Role.ADMIN, oleh)
    respons = client.post(URL, json=_badan(siap, **ubah))
    assert respons.status_code == 201, respons.text
    return baca(siap, respons.json()["session_id"], sebagai=oleh, peran=Role.ADMIN)


def baca(siap, sesi_id, *, sebagai: str = "Admin Satu", peran: Role = Role.ADMIN, **query) -> dict:
    """Halaman sesi manual seperti yang dibaca SCRUM-235."""
    respons = orang(siap, peran, sebagai).get(f"{URL}/{sesi_id}", params=query)
    assert respons.status_code == 200, respons.text
    return respons.json()


def slot(detail: dict, nomor_kasus: int, percobaan: int = 1) -> dict:
    """Slot percobaan ke-`percobaan` dari kasus ke-`nomor_kasus` (urut kode kasus)."""
    kasus = sorted(detail["cases"], key=lambda c: c["case_code"])[nomor_kasus]
    return {"snapshot_item_id": kasus["snapshot_item_id"], "attempt_no": percobaan}


def isi(
    siap,
    sesi_id,
    tempat: dict,
    teks: str | None,
    *,
    oleh: str,
    peran: Role = Role.CONTRIBUTOR,
    **ubah,
):
    """PUT satu jawaban sebagai `oleh`. Mengembalikan respons mentah supaya tes bisa cek galat."""
    badan = {**tempat, "answer_md": teks, "platform_refused": False, "confirm": False, **ubah}
    return orang(siap, peran, oleh).put(f"{URL}/{sesi_id}/answers", json=badan)


def riwayat(siap, sesi_id, tempat: dict, *, sebagai: str = "Admin Satu", peran: Role = Role.ADMIN):
    """Riwayat jawaban satu slot, versi lama dulu."""
    jalur = f"{URL}/{sesi_id}/answers/{tempat['snapshot_item_id']}/{tempat['attempt_no']}/history"
    return orang(siap, peran, sebagai).get(jalur)


def isi_semua(siap, sesi_id, *, oleh: str = "Kontributor Satu") -> None:
    """Isi setiap slot yang masih kosong, seperti tim legal menuntaskan satu sesi."""
    detail = baca(siap, sesi_id)
    for kasus in detail["cases"]:
        for percobaan in kasus["attempts"]:
            if not percobaan["filled"]:
                tempat = {
                    "snapshot_item_id": kasus["snapshot_item_id"],
                    "attempt_no": percobaan["repetition_no"],
                }
                respons = isi(
                    siap,
                    sesi_id,
                    tempat,
                    f"Jawaban {kasus['case_code']} #{percobaan['repetition_no']}",
                    oleh=oleh,
                )
                assert respons.status_code == 200, respons.text
