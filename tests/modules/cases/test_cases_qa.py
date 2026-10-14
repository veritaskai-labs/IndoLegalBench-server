"""Sisi kasus yang belum terjaga. PBI-3 (SCRUM-70), sub task QA (SCRUM-111).

test_cases.py dan test_validation.py di PR ini sudah rapat: ID bentrok
menyebut suite pemiliknya, pasal wajib, split_tag wajib, suite arsip
ditolak, Author lain ditolak. Berkas ini sengaja tidak mengulang satu pun
di antaranya dan hanya mengisi empat celah yang tersisa.

1. Izin baca. Test peran yang ada semuanya menguji peran yang ditolak
   menulis. Tidak ada satu pun yang mencoba peran yang seharusnya BOLEH
   membaca, sehingga izin yang terlalu sempit lolos tanpa suara.

2. Field yang tidak boleh diterima dari klien. status, version, dan
   completeness_pct ditentukan server. CaseWrite tidak memakai
   extra="forbid", jadi field asing dibuang diam-diam. Perilaku itu aman
   sekarang, tetapi tidak ada yang menguncinya.

3. AC-4. Kasus tidak boleh diajukan review tanpa jebakan. Aturan itu
   belum bisa ditegakkan karena tidak ada jalur apa pun dari draft ke
   in_review.

4. Tepi atas batas panjang. PR ini sudah menguji nilai yang melewati
   batas; yang belum diuji adalah nilai tepat di batas, tempat kesalahan
   off-by-one biasanya muncul.
"""

import uuid

import pytest

from app.main import app
from app.modules.cases.models import CaseStatus
from app.shared.security import Role

TIDAK_ADA = uuid.UUID("00000000-0000-0000-0000-0000000000ff")

BATAS_JUDUL = 300
BATAS_PERTANYAAN = 20000
BATAS_PASAL = 40
BATAS_KODE = 64
TAHUN_MIN, TAHUN_MAKS = 1, 9999


def _badan(**ubah) -> dict:
    badan = {
        "case_code": "QA-001",
        "identity": {
            "title": "PHK sepihak",
            "question": "Apakah PHK tanpa surat sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [
            {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"}
        ],
        "answer_criteria": {
            "must_contain": ["surat"],
            "must_not_contain": [],
            "expected_conclusion": "tidak sah",
        },
        "traps": [
            {"description": "Mencampur upah dan pesangon", "expected_model_behavior": "menolak"}
        ],
        "split_tag": "dev",
    }
    badan.update(ubah)
    return badan


def _suite(client, nama: str = "Ketenagakerjaan QA") -> str:
    respons = client.post("/suites", json={"name": nama, "description": None})
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def _buat(client, suite_id: str, **ubah):
    return client.post(f"/suites/{suite_id}/cases", json=_badan(**ubah))


# ---------------------------------------------------------------------
# 1. Izin, diambil dari docs/pbi1/roles-matrix.svg baris "Case"
# ---------------------------------------------------------------------

# Create: Author, Admin. Read: Author, Reviewer, Admin. Update dan Delete:
# Author (miliknya sendiri) dan Admin. Viewer tidak boleh apa pun.
OPERASI_CASE = [
    ("case-buat", "POST", f"/suites/{TIDAK_ADA}/cases", {Role.AUTHOR, Role.ADMIN}, _badan()),
    (
        "case-daftar",
        "GET",
        f"/suites/{TIDAK_ADA}/cases",
        {Role.AUTHOR, Role.REVIEWER, Role.ADMIN},
        None,
    ),
    ("case-detail", "GET", f"/cases/{TIDAK_ADA}", {Role.AUTHOR, Role.REVIEWER, Role.ADMIN}, None),
    ("case-ubah", "PUT", f"/cases/{TIDAK_ADA}", {Role.AUTHOR, Role.ADMIN}, _badan()),
]

# Dua sel matriks yang saat ini gagal karena cacat yang sudah dilaporkan,
# bukan karena testnya keliru: penjaga baca di cases/router.py memakai
# require_roles(AUTHOR, ADMIN) untuk kedua GET, sementara
# docs/pbi1/roles-matrix.svg mengizinkan Reviewer membaca kasus. Cacat ini
# ikut masuk lewat #19 dan keputusannya, melebarkan penjaga atau mengubah
# matriks, ditunda sampai setelah UAT.
#
# Ditandai xfail strict, bukan dilewati: testnya tetap dijalankan, kegagalan
# yang sudah diketahui tidak memerahkan CI tim, dan begitu izinnya diperbaiki
# test ini berubah jadi XPASS yang dihitung gagal, sehingga penandaannya tidak
# bisa tertinggal diam-diam.
ALASAN_REVIEWER = (
    "Reviewer ditolak membaca kasus, berbeda dengan docs/pbi1/roles-matrix.svg "
    "(SCRUM-97). Ikut dari #19. Keputusan melebarkan penjaga atau mengubah "
    "matriks ditunda ke setelah UAT. Menghalangi PBI-6 Review Independen."
)
MENUNGGU_KEPUTUSAN = {
    ("case-daftar", Role.REVIEWER),
    ("case-detail", Role.REVIEWER),
}


def _kombinasi():
    for op in OPERASI_CASE:
        for peran in Role:
            tanda = (
                [pytest.mark.xfail(strict=True, reason=ALASAN_REVIEWER)]
                if (op[0], peran) in MENUNGGU_KEPUTUSAN
                else []
            )
            yield pytest.param(op, peran, id=f"{op[0]}--{peran.value}", marks=tanda)


@pytest.mark.parametrize("operasi,peran", list(_kombinasi()))
def test_izin_kasus_sesuai_matriks_peran(as_role, operasi, peran):
    """Otorisasi diperiksa sebelum baris dicari, jadi UUID yang tidak ada sudah cukup."""
    nama, metode, jalur, boleh, badan = operasi
    client = as_role(peran)

    kirim = getattr(client, metode.lower())
    respons = kirim(jalur) if badan is None else kirim(jalur, json=badan)

    if peran in boleh:
        assert respons.status_code != 403, (
            f"{peran.value} seharusnya boleh {nama} menurut docs/pbi1/roles-matrix.svg, "
            f"tetapi ditolak 403. Reviewer yang tidak bisa membuka kasus membuat PBI-6 "
            f"Review Independen tidak mungkin dijalankan."
        )
    else:
        assert respons.status_code == 403, (
            f"{peran.value} seharusnya ditolak untuk {nama} menurut "
            f"docs/pbi1/roles-matrix.svg, tetapi jawabannya {respons.status_code}."
        )


# ---------------------------------------------------------------------
# 2. Field yang ditentukan server tidak boleh bisa disetir klien
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "titipan",
    [
        {"status": "approved"},
        {"completeness_pct": 100},
        {"version": 99},
        {"created_by": "00000000-0000-0000-0000-0000000000aa"},
        {"id": "00000000-0000-0000-0000-0000000000bb"},
    ],
    ids=["status", "completeness_pct", "version", "created_by", "id"],
)
def test_field_milik_server_diabaikan_saat_membuat(as_role, titipan):
    """Menitipkan field milik server pada badan permintaan tidak boleh mengubah apa pun.

    CaseWrite tidak memakai extra="forbid", jadi field asing dibuang.
    Itu aman, tetapi hanya selama tidak ada yang menambahkan field
    tersebut ke schema tulis. Test ini yang akan merah kalau itu terjadi.
    """
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(client, suite_id, **titipan)

    assert respons.status_code == 201
    badan = respons.json()
    assert badan["status"] == CaseStatus.DRAFT.value
    assert badan["version"] == 1
    assert badan["id"] != titipan.get("id")


def test_field_milik_server_diabaikan_saat_mengubah(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)
    kasus = _buat(client, suite_id).json()

    respons = client.put(
        f"/cases/{kasus['id']}",
        json=_badan(
            status="approved",
            version=99,
            identity={
                "title": "Judul baru",
                "question": "Pertanyaan baru?",
                "category": "ketenagakerjaan",
            },
        ),
    )

    assert respons.status_code == 200
    badan = respons.json()
    assert badan["status"] == CaseStatus.DRAFT.value, "status berpindah karena titipan klien"
    assert badan["version"] == 1, "draf tetap di versi 1, dan titipan klien diabaikan"


# ---------------------------------------------------------------------
# 3. AC-4, jebakan wajib sebelum kasus diajukan review
# ---------------------------------------------------------------------

JALUR_CASES_SEKARANG = {
    "/suites/{suite_id}/cases",
    "/cases/{case_id}",
}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Tombol dan endpoint pengajuan review dijadwalkan di PBI-6 "
        "(Review Independen), sesuai catatan pada SCRUM-109. Selama transisi "
        "draft -> in_review belum ada, AC-4 PBI-3 tidak punya aksi untuk "
        "dijaga. Test ini menunggu di sini supaya aturannya tidak hilang saat "
        "PBI-6 dikerjakan."
    ),
)
def test_ac4_ada_jalan_untuk_mengajukan_kasus_ke_review():
    """AC-4: "Kasus tidak bisa diajukan untuk direview jika belum memiliki
    setidaknya satu jebakan."

    Status in_review sudah ada di enum, tetapi tidak ada satu pun endpoint
    yang bisa memindahkan kasus ke sana: CaseWrite tidak memuat status,
    dan service selalu menyimpan DRAFT. Selama begitu, AC-4 tidak bisa
    ditegakkan maupun dilanggar, dan tidak ada yang bisa diuji.

    Test ini sengaja dibiarkan merah supaya celahnya terbaca, bukan
    hilang di catatan. Nama endpoint-nya tidak ditebak di sini; yang
    diperiksa hanya apakah ada jalur baru di luar keempat operasi CRUD.
    """
    assert CaseStatus.IN_REVIEW.value == "in_review"

    jalur_cases = {
        getattr(rute, "path", "") for rute in app.routes if "cases" in getattr(rute, "path", "")
    }
    tambahan = jalur_cases - JALUR_CASES_SEKARANG

    assert tambahan, (
        "Belum ada transisi draft -> in_review di mana pun, sehingga AC-4 PBI-3 "
        "(minimal satu jebakan sebelum kasus diajukan review) tidak bisa ditegakkan. "
        "Endpoint pengajuan review belum ada di SCRUM-106 maupun SCRUM-107."
    )


# ---------------------------------------------------------------------
# 4. Tepat di batas. Sisi di atas batas sudah diuji di test_cases.py
# ---------------------------------------------------------------------


@pytest.mark.parametrize("panjang", [BATAS_JUDUL - 1, BATAS_JUDUL])
def test_judul_tepat_di_batas_diterima(as_role, panjang):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(
        client,
        suite_id,
        identity={"title": "j" * panjang, "question": "Sah?", "category": None},
    )

    assert respons.status_code == 201
    assert len(respons.json()["identity"]["title"]) == panjang


def test_pertanyaan_tepat_di_batas_diterima(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(
        client,
        suite_id,
        identity={"title": "Panjang", "question": "p" * BATAS_PERTANYAAN, "category": None},
    )

    assert respons.status_code == 201


@pytest.mark.parametrize("tahun", [TAHUN_MIN, TAHUN_MAKS])
def test_tahun_di_kedua_ujung_rentang_diterima(as_role, tahun):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(
        client,
        suite_id,
        legal_refs=[
            {"regulation_type": "uu", "regulation_number": "13", "year": tahun, "pasal": "151"}
        ],
    )

    assert respons.status_code == 201


@pytest.mark.parametrize("tahun", [TAHUN_MIN - 1, TAHUN_MAKS + 1])
def test_tahun_di_luar_rentang_ditolak(as_role, tahun):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(
        client,
        suite_id,
        legal_refs=[
            {"regulation_type": "uu", "regulation_number": "13", "year": tahun, "pasal": "151"}
        ],
    )

    assert respons.status_code == 422


def test_pasal_tepat_di_batas_diterima(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(
        client,
        suite_id,
        legal_refs=[
            {
                "regulation_type": "uu",
                "regulation_number": "13",
                "year": 2003,
                "pasal": "p" * BATAS_PASAL,
            }
        ],
    )

    assert respons.status_code == 201


@pytest.mark.parametrize("panjang", [2, BATAS_KODE])
def test_case_code_tepat_di_batas_pola_diterima(as_role, panjang):
    # Polanya ^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$, jadi panjang sah 2 sampai 64.
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(client, suite_id, case_code="Q" + "1" * (panjang - 1))

    assert respons.status_code == 201


def test_case_code_satu_karakter_melewati_batas_ditolak(as_role):
    client = as_role(Role.AUTHOR)
    suite_id = _suite(client)

    respons = _buat(client, suite_id, case_code="Q" + "1" * BATAS_KODE)

    assert respons.status_code == 422
