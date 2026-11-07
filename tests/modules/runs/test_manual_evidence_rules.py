"""Aturan murni bukti jawaban manual. PBI-22 (SCRUM-176) AC7; SCRUM-233.

D9 (ERD Jawaban Manual), tabel attempt_evidence: bukti opsional berupa berkas tangkapan
layar atau tautan, maksimal 3 per jawaban (attempt). Berkas hanya PNG, JPG, atau PDF.
Ukuran bebas dengan batas teknis dari tim. Tipe divalidasi di server dari isi berkas
(magic bytes), bukan hanya dari ekstensi atau Content-Type yang dikirim browser, sesuai
tiket: berkas .png yang isinya bukan PNG tetap ditolak.

Siapa yang boleh melihat bukti masih TBA (D9 [PENDING]). Default-nya semua yang bisa
membuka sesi manual (Admin dan Kontributor, D10), dibuat flag konfigurasi supaya bisa
dipersempit ke Admin saja tanpa ubah kode.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import pytest

from app.shared.exceptions import ConflictError, ValidationError
from app.shared.security import Role

# Awal berkas yang sah per format (signature resmi masing-masing).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
PDF = b"%PDF-1.7\n" + b"\x00" * 24


@pytest.fixture
def aturan():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs import manual_evidence_rules

    return manual_evidence_rules


@pytest.mark.parametrize(
    ("isi", "dikirim", "tipe"),
    [
        (PNG, "image/png", "image/png"),
        (JPEG, "image/jpeg", "image/jpeg"),
        (PDF, "application/pdf", "application/pdf"),
        # Browser lama mengirim image/jpg untuk .jpg; isinya tetap JPEG.
        (JPEG, "image/jpg", "image/jpeg"),
    ],
    ids=["png", "jpg", "pdf", "alias-jpg"],
)
def test_tipe_berkas_dibaca_dari_isi(aturan, isi, dikirim, tipe):
    """Positif: AC7, tipe disimpan dari magic bytes, jadi kolom mime_type D9 selalu sah."""
    # Act
    hasil = aturan.detect_file_type(isi, declared=dikirim)

    # Assert
    assert hasil == tipe


@pytest.mark.parametrize(
    ("isi", "dikirim", "kode"),
    [
        # Negatif: GIF dan teks bukan format yang diizinkan, apa pun Content-Type-nya.
        (b"GIF89a" + b"\x00" * 24, "image/png", "EVIDENCE_TYPE_NOT_ALLOWED"),
        (b"<html><script>", "image/png", "EVIDENCE_TYPE_NOT_ALLOWED"),
        # Negatif: isi PDF yang diberi label PNG; ekstensi saja tidak dipercaya.
        (PDF, "image/png", "EVIDENCE_TYPE_MISMATCH"),
        # Negatif: Content-Type di luar daftar walau isinya PNG.
        (PNG, "application/octet-stream", "EVIDENCE_TYPE_MISMATCH"),
    ],
    ids=["gif", "html", "pdf-berlabel-png", "octet-stream"],
)
def test_tipe_berkas_ditolak_bila_isi_tidak_sah(aturan, isi, dikirim, kode):
    """Negatif: AC7, validasi tipe di server dari isi berkas, bukan hanya ekstensi."""
    # Act
    with pytest.raises(ValidationError) as galat:
        aturan.detect_file_type(isi, declared=dikirim)

    # Assert
    assert galat.value.code == kode


@pytest.mark.parametrize(
    ("ukuran", "kode"),
    [
        (0, "EVIDENCE_EMPTY"),
        (1025, "EVIDENCE_TOO_LARGE"),
    ],
    ids=["kosong", "lewat-batas"],
)
def test_ukuran_berkas_ditolak_di_luar_batas(aturan, ukuran, kode):
    """Negatif: AC7, berkas kosong tidak berguna; di atas batas teknis tim ditolak."""
    # Act
    with pytest.raises(ValidationError) as galat:
        aturan.ensure_size(ukuran, limit_bytes=1024)

    # Assert
    assert galat.value.code == kode
    assert galat.value.details.get("limit_bytes") in (None, 1024)


@pytest.mark.parametrize("ukuran", [1, 1024], ids=["satu-byte", "tepat-batas"])
def test_ukuran_tepat_di_batas_diterima(aturan, ukuran):
    """Edge: AC7, batas teknis bersifat inklusif."""
    # Act / Assert: tidak melempar
    aturan.ensure_size(ukuran, limit_bytes=1024)


@pytest.mark.parametrize("sudah_ada", [0, 1, 2], ids=["0", "1", "2"])
def test_bukti_boleh_ditambah_sampai_tiga(aturan, sudah_ada):
    """Positif: AC7, D9 maksimal 3 bukti per jawaban."""
    # Act / Assert: tidak melempar
    aturan.ensure_room(sudah_ada)


def test_bukti_keempat_ditolak(aturan):
    """Negatif: AC7, bukti keempat ditolak 409 sebelum menyentuh penyimpanan."""
    # Act
    with pytest.raises(ConflictError) as galat:
        aturan.ensure_room(3)

    # Assert
    assert galat.value.code == "EVIDENCE_LIMIT_REACHED"
    assert aturan.MAX_EVIDENCE_PER_ATTEMPT == 3


@pytest.mark.parametrize(
    ("tautan", "hasil"),
    [
        ("https://chat.example.com/share/abc", "https://chat.example.com/share/abc"),
        ("  http://example.com/x?y=1  ", "http://example.com/x?y=1"),
    ],
    ids=["https", "http-dirapikan"],
)
def test_tautan_http_diterima(aturan, tautan, hasil):
    """Positif: AC7, bukti berupa tautan percakapan di platform; spasi tepi dibuang."""
    # Act / Assert
    assert aturan.normalize_link(tautan) == hasil


@pytest.mark.parametrize(
    "tautan",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "ftp://example.com/berkas",
        "https://",
        "bukan tautan",
        "https://example.com/" + "a" * 2030,
    ],
    ids=["kosong", "spasi", "javascript", "ftp", "tanpa-host", "teks", "lewat-2048"],
)
def test_tautan_tidak_sah_ditolak(aturan, tautan):
    """Negatif: AC7, hanya http(s) dengan host, maksimal 2048 karakter (D9 varchar(2048))."""
    # Act
    with pytest.raises(ValidationError) as galat:
        aturan.normalize_link(tautan)

    # Assert
    assert galat.value.code == "EVIDENCE_LINK_INVALID"


@pytest.mark.parametrize(
    ("kebijakan", "peran", "boleh"),
    [
        # Positif: default TBA = semua yang bisa membuka sesi manual (D10).
        ("session_viewers", Role.ADMIN, True),
        ("session_viewers", Role.CONTRIBUTOR, True),
        # Positif: flag dipersempit ke Admin saja.
        ("admin_only", Role.ADMIN, True),
        ("admin_only", Role.CONTRIBUTOR, False),
        # Negatif: Viewer dan Dev AI memang tidak bisa membuka sesi manual (D10).
        ("session_viewers", Role.VIEWER, False),
        ("session_viewers", Role.DEVELOPER_AI, False),
    ],
)
def test_siapa_boleh_melihat_bukti_mengikuti_flag(aturan, kebijakan, peran, boleh):
    """Positif: AC7, siapa yang boleh melihat bukti masih TBA, jadi dibuat flag."""
    # Act / Assert
    assert aturan.can_view_evidence(peran, policy=kebijakan) is boleh


@pytest.mark.parametrize(
    ("nama", "hasil"),
    [
        ("tangkapan layar 1.png", "tangkapan layar 1.png"),
        # Negatif: jalur folder dibuang, hanya nama berkasnya.
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\tim\\bukti.jpg", "bukti.jpg"),
        # Negatif: karakter kontrol dan tanda kutip bisa memecah header Content-Disposition.
        ('bu"k\r\nti.png', "bukti.png"),
        # Edge: nama kosong diganti nama baku.
        ("", "bukti"),
        (None, "bukti"),
    ],
    ids=["biasa", "jalur-unix", "jalur-windows", "kontrol", "kosong", "none"],
)
def test_nama_berkas_dirapikan_untuk_unduhan(aturan, nama, hasil):
    """Positif/Negatif: AC7, nama asli berkas aman dipakai di header unduhan."""
    # Act / Assert
    assert aturan.safe_file_name(nama) == hasil


def test_nama_berkas_panjang_dipotong_tanpa_kehilangan_ekstensi(aturan):
    """Edge: kolom file_name varchar(255); ekstensi tetap ada supaya berkas bisa dibuka."""
    # Act
    hasil = aturan.safe_file_name("a" * 400 + ".pdf")

    # Assert
    assert len(hasil) == 255
    assert hasil.endswith(".pdf")


def test_nama_berkas_panjang_tanpa_ekstensi_dipotong(aturan):
    """Edge: tanpa ekstensi yang wajar, nama cukup dipotong ke 255 karakter."""
    # Act / Assert
    assert aturan.safe_file_name("b" * 300) == "b" * 255
