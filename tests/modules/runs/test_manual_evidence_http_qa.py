"""QA bukti jawaban manual. PBI-22 (SCRUM-176) AC7 dan audit AC9, SCRUM-238.

Lewat HTTP seperti kartu jawaban (SCRUM-237): unggah, daftar, unduh, dan hapus bukti.
Keputusan PO (8 Nov): bukti untuk Admin dan Kontributor, yaitu siapa pun yang bisa membuka
sesi manual; Viewer belum.

Desain tes:
- boundary value: jumlah bukti per jawaban (3 diterima, ke-4 ditolak), ukuran berkas
  (batas-1, batas, batas+1) dengan batas diperkecil lewat setting, panjang tautan (2048/2049);
- equivalence partitioning tipe berkas: PNG/JPG/PDF sah, tipe lain ditolak, isi PNG berlabel
  PDF ditolak, label generik (octet-stream) diterima bila isinya sah;
- keamanan: tautan selain http(s), IDOR antarsesi, nama berkas berbahaya di header unduhan,
  audit tanpa isi berkas maupun url.

Label: Positif = diterima, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.runs.manual_evidence import ACTION_EVIDENCE_ADDED, ACTION_EVIDENCE_REMOVED
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.runs.manual_qa import URL, buat_sesi, isi_semua, orang, slot

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 64
GIF = b"GIF89a" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
EXE = b"MZ" + b"\x00" * 64


def _jalur(sesi_id, tempat) -> str:
    return f"{URL}/{sesi_id}/answers/{tempat['snapshot_item_id']}/{tempat['attempt_no']}/evidence"


def unggah(siap, sesi_id, tempat, *, berkas=None, url=None, oleh="Rina", peran=Role.CONTRIBUTOR):
    """POST satu bukti. berkas = (nama, isi, content_type)."""
    client = orang(siap, peran, oleh)
    files = {"file": berkas} if berkas is not None else None
    data = {"url": url} if url is not None else None
    return client.post(_jalur(sesi_id, tempat), files=files, data=data)


def daftar(siap, sesi_id, tempat, *, oleh="Admin Satu", peran=Role.ADMIN):
    return orang(siap, peran, oleh).get(_jalur(sesi_id, tempat))


@pytest.fixture
def sesi(siap_manual):
    """Sesi 3 kasus x 1 percobaan; bukti dipasang di slot kasus pertama."""
    detail = buat_sesi(siap_manual, repeat_count=1)
    return siap_manual, detail["id"], slot(detail, 0)


@pytest.fixture
def setting(monkeypatch):
    """Ganti setting aplikasi untuk satu tes, misalnya batas ukuran atau kebijakan lihat bukti."""

    def ganti(**nilai):
        baru = get_settings().model_copy(update=nilai)
        monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: baru)

    return ganti


def test_tiga_bukti_campuran_diterima_dan_yang_keempat_ditolak(sesi):
    """Boundary: AC7, maksimal 3 bukti per jawaban (berkas dan tautan dihitung bersama)."""
    # Arrange
    siap, sid, tempat = sesi

    # Act
    tiga = [
        unggah(siap, sid, tempat, berkas=("chat.png", PNG, "image/png")),
        unggah(siap, sid, tempat, url="https://legalhero.id/chat/123"),
        unggah(siap, sid, tempat, berkas=("transkrip.pdf", PDF, "application/pdf")),
    ]
    keempat = unggah(siap, sid, tempat, berkas=("lagi.jpg", JPG, "image/jpeg"))

    # Assert
    assert [r.status_code for r in tiga] == [201, 201, 201], [r.text for r in tiga]
    assert (keempat.status_code, keempat.json()["code"]) == (409, "EVIDENCE_LIMIT_REACHED")
    assert len(daftar(siap, sid, tempat).json()) == 3


@pytest.mark.parametrize(
    ("berkas", "harapan"),
    [
        pytest.param(("a.png", PNG, "image/png"), (201, None), id="PNG"),
        pytest.param(("a.jpg", JPG, "image/jpeg"), (201, None), id="JPG"),
        pytest.param(("a.jpg", JPG, "image/jpg"), (201, None), id="JPG label browser lama"),
        pytest.param(("a.pdf", PDF, "application/pdf"), (201, None), id="PDF"),
        pytest.param(
            ("a.png", PNG, "application/octet-stream"), (201, None), id="label generik, isi sah"
        ),
        pytest.param(("a.gif", GIF, "image/gif"), (422, "EVIDENCE_TYPE_NOT_ALLOWED"), id="GIF"),
        pytest.param(
            ("a.svg", SVG, "image/svg+xml"),
            (422, "EVIDENCE_TYPE_NOT_ALLOWED"),
            id="SVG berisi script",
        ),
        pytest.param(
            ("a.pdf", EXE, "application/pdf"),
            (422, "EVIDENCE_TYPE_NOT_ALLOWED"),
            id="EXE berlabel PDF",
        ),
        pytest.param(
            ("a.pdf", PNG, "application/pdf"),
            (422, "EVIDENCE_TYPE_MISMATCH"),
            id="isi PNG berlabel PDF",
        ),
        pytest.param(("a.png", b"", "image/png"), (422, "EVIDENCE_EMPTY"), id="berkas kosong"),
    ],
)
def test_tipe_berkas_dibaca_dari_isinya(sesi, berkas, harapan):
    """Equivalence partitioning: AC7, hanya PNG/JPG/PDF, dikenali dari isi berkas."""
    # Arrange
    siap, sid, tempat = sesi

    # Act
    respons = unggah(siap, sid, tempat, berkas=berkas)

    # Assert
    kode_status, kode_galat = harapan
    assert respons.status_code == kode_status, respons.text
    if kode_galat:
        assert respons.json()["code"] == kode_galat


@pytest.mark.parametrize(
    ("ukuran", "harapan"),
    [
        pytest.param(2047, 201, id="batas-1"),
        pytest.param(2048, 201, id="batas"),
        pytest.param(2049, 413, id="batas+1"),
    ],
)
def test_batas_ukuran_berkas(sesi, setting, ukuran, harapan):
    """Boundary: AC7 'ukuran bebas dengan batas teknis'; batas diperkecil jadi 2048 byte."""
    # Arrange
    siap, sid, tempat = sesi
    setting(manual_evidence_max_bytes=2048)
    isi = PNG + b"\x00" * (ukuran - len(PNG))

    # Act
    respons = unggah(siap, sid, tempat, berkas=("besar.png", isi, "image/png"))

    # Assert
    assert respons.status_code == harapan, respons.text
    if harapan == 413:
        assert respons.json()["code"] == "EVIDENCE_TOO_LARGE"


@pytest.mark.parametrize(
    ("url", "diterima"),
    [
        pytest.param("https://legalhero.id/chat/1", True, id="https"),
        pytest.param("http://justisio.com/q/1", True, id="http"),
        pytest.param(
            "https://contoh.test/" + "a" * (2048 - len("https://contoh.test/")),
            True,
            id="2048 karakter",
        ),
        pytest.param(
            "https://contoh.test/" + "a" * (2049 - len("https://contoh.test/")),
            False,
            id="2049 karakter",
        ),
        pytest.param("javascript:alert(1)", False, id="javascript:"),
        pytest.param("data:text/html,<script>alert(1)</script>", False, id="data:"),
        pytest.param("ftp://legalhero.id/berkas", False, id="ftp:"),
        pytest.param("https://", False, id="tanpa host"),
        pytest.param("https://[rusak", False, id="url rusak"),
        pytest.param("legalhero.id/chat", False, id="tanpa skema"),
    ],
)
def test_tautan_bukti_hanya_http_atau_https_yang_lengkap(sesi, url, diterima):
    """Keamanan + boundary: AC7, tautan yang bisa menjalankan script tidak pernah tersimpan."""
    # Arrange
    siap, sid, tempat = sesi

    # Act
    respons = unggah(siap, sid, tempat, url=url)

    # Assert
    if diterima:
        assert respons.status_code == 201, respons.text
    else:
        assert (respons.status_code, respons.json()["code"]) == (422, "EVIDENCE_LINK_INVALID")
        assert daftar(siap, sid, tempat).json() == []


@pytest.mark.parametrize(
    "isi",
    [
        pytest.param(
            {"berkas": ("a.png", PNG, "image/png"), "url": "https://legalhero.id"},
            id="berkas dan tautan",
        ),
        pytest.param({}, id="tanpa keduanya"),
    ],
)
def test_satu_permintaan_berisi_tepat_satu_bukti(sesi, isi):
    """Negatif: AC7, satu unggahan = satu berkas atau satu tautan."""
    # Arrange
    siap, sid, tempat = sesi

    # Act
    respons = unggah(siap, sid, tempat, **isi)

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (422, "EVIDENCE_KIND_REQUIRED")


def test_unduhan_selalu_lampiran_dengan_nama_yang_aman(sesi):
    """Keamanan: berkas diunduh sebagai lampiran (tidak dibuka di origin aplikasi), isinya
    sama persis, dan nama berkas dengan jalur folder, kutip, atau CR/LF tidak memecah header."""
    # Arrange
    siap, sid, tempat = sesi
    nama_jahat = '..\\..\\windows/"rahasia"\r\nSet-Cookie: x=1.png'
    dibuat = unggah(siap, sid, tempat, berkas=(nama_jahat, PNG, "image/png")).json()

    # Act
    respons = orang(siap, Role.CONTRIBUTOR, "Budi").get(f"{URL}/{sid}/evidence/{dibuat['id']}/file")

    # Assert
    assert respons.status_code == 200
    assert respons.content == PNG
    assert respons.headers["content-type"] == "image/png"
    assert respons.headers["x-content-type-options"] == "nosniff"
    disposisi = respons.headers["content-disposition"]
    assert disposisi.startswith("attachment;")
    assert "\r" not in disposisi and "\n" not in disposisi
    assert "set-cookie" not in {k.lower() for k in respons.headers}
    assert "%2F" not in disposisi and "%5C" not in disposisi


@pytest.mark.parametrize(
    ("peran", "boleh"),
    [
        pytest.param(Role.ADMIN, True, id="admin"),
        pytest.param(Role.CONTRIBUTOR, True, id="kontributor"),
        pytest.param(Role.VIEWER, False, id="viewer"),
        pytest.param(Role.DEVELOPER_AI, False, id="developer AI"),
    ],
)
def test_bukti_untuk_admin_dan_kontributor_sesuai_keputusan_po(sesi, peran, boleh):
    """Positif + Negatif: keputusan PO AC7 (8 Nov), bukti untuk siapa pun yang bisa membuka
    sesi manual. Viewer dan Developer AI tidak bisa mengunggah, melihat, atau mengunduh."""
    # Arrange
    siap, sid, tempat = sesi
    ada = unggah(siap, sid, tempat, berkas=("chat.png", PNG, "image/png")).json()
    nama = f"QA {peran.value}"

    # Act
    tambah = unggah(siap, sid, tempat, url="https://legalhero.id/x", oleh=nama, peran=peran)
    lihat = daftar(siap, sid, tempat, oleh=nama, peran=peran)
    unduh = orang(siap, peran, nama).get(f"{URL}/{sid}/evidence/{ada['id']}/file")

    # Assert
    kode = [tambah.status_code, lihat.status_code, unduh.status_code]
    assert kode == ([201, 200, 200] if boleh else [403, 403, 403]), [
        tambah.text,
        lihat.text,
        unduh.text,
    ]


def test_kebijakan_admin_saja_menyembunyikan_bukti_dari_kontributor(sesi, setting):
    """Edge: setting MANUAL_EVIDENCE_VISIBILITY=admin_only (cadangan bila PO berubah pikiran).
    Kontributor tidak bisa melihat daftar maupun mengunduh; Admin tetap bisa."""
    # Arrange
    siap, sid, tempat = sesi
    ada = unggah(siap, sid, tempat, berkas=("chat.png", PNG, "image/png")).json()
    setting(manual_evidence_visibility="admin_only")

    # Act
    kontributor = daftar(siap, sid, tempat, oleh="Budi", peran=Role.CONTRIBUTOR)
    unduh = orang(siap, Role.CONTRIBUTOR, "Budi").get(f"{URL}/{sid}/evidence/{ada['id']}/file")
    admin = daftar(siap, sid, tempat)

    # Assert
    assert kontributor.status_code == 404
    assert unduh.status_code == 404
    assert admin.status_code == 200 and len(admin.json()) == 1


def test_bukti_sesi_lain_tidak_bisa_diunduh_atau_dihapus_lewat_sesi_ini(siap_manual):
    """Negatif (IDOR): id bukti sesi B dipakai di jalur sesi A, ditolak 404 dan bukti B utuh."""
    # Arrange
    sesi_a = buat_sesi(siap_manual, repeat_count=1, label="Sesi A")
    sesi_b = buat_sesi(siap_manual, repeat_count=1, label="Sesi B")
    tempat_b = slot(sesi_b, 0)
    bukti_b = unggah(siap_manual, sesi_b["id"], tempat_b, berkas=("b.png", PNG, "image/png")).json()
    admin = orang(siap_manual, Role.ADMIN, "Admin Satu")

    # Act
    unduh = admin.get(f"{URL}/{sesi_a['id']}/evidence/{bukti_b['id']}/file")
    hapus = admin.delete(f"{URL}/{sesi_a['id']}/evidence/{bukti_b['id']}")

    # Assert
    assert (unduh.status_code, unduh.json()["code"]) == (404, "EVIDENCE_NOT_FOUND")
    assert hapus.status_code == 404
    assert len(daftar(siap_manual, sesi_b["id"], tempat_b).json()) == 1


def test_bukti_tidak_bisa_ditambah_atau_dihapus_setelah_sesi_selesai(sesi):
    """Negatif: AC8, sesi Selesai final, termasuk buktinya."""
    # Arrange
    siap, sid, tempat = sesi
    ada = unggah(siap, sid, tempat, berkas=("chat.png", PNG, "image/png")).json()
    isi_semua(siap, sid)
    admin = orang(siap, Role.ADMIN, "Admin Satu")
    assert admin.post(f"{URL}/{sid}/finalize").status_code == 200

    # Act
    tambah = unggah(siap, sid, tempat, url="https://legalhero.id/telat")
    hapus = orang(siap, Role.ADMIN, "Admin Satu").delete(f"{URL}/{sid}/evidence/{ada['id']}")

    # Assert
    assert (tambah.status_code, tambah.json()["code"]) == (409, "MANUAL_SESSION_FINALIZED")
    assert (hapus.status_code, hapus.json()["code"]) == (409, "MANUAL_SESSION_FINALIZED")
    assert len(daftar(siap, sid, tempat).json()) == 1


def test_tambah_dan_hapus_bukti_tercatat_di_audit_tanpa_isi_berkas_atau_url(sesi):
    """Positif + keamanan: AC9 dan PBI-18, audit mencatat siapa dan bukti mana, tanpa menyalin
    isi berkas maupun url ke audit_logs."""
    # Arrange
    siap, sid, tempat = sesi
    url = "https://legalhero.id/chat/rahasia-klien"
    tautan = unggah(siap, sid, tempat, url=url).json()

    # Act
    hapus = orang(siap, Role.ADMIN, "Admin Satu").delete(f"{URL}/{sid}/evidence/{tautan['id']}")
    jejak = siap.db.scalars(select(AuditLog).where(AuditLog.entity_id == uuid.UUID(sid))).all()

    # Assert
    assert hapus.status_code == 204
    aksi = [j.action for j in jejak]
    assert ACTION_EVIDENCE_ADDED in aksi and ACTION_EVIDENCE_REMOVED in aksi
    bukti = [j for j in jejak if j.action in (ACTION_EVIDENCE_ADDED, ACTION_EVIDENCE_REMOVED)]
    assert all(url not in str(j.after) + str(j.before) for j in bukti)
