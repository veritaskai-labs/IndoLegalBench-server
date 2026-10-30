"""Daftar produk AI per jenis dan per peran. PBI-23 AC6-AC8, SCRUM-201.

Dua daftar yang diuji:
- GET /admin/providers: daftar lengkap untuk Admin, bisa disaring jenis dan status (AC6).
- GET /ai-products?purpose=...: daftar pilihan di form pengukuran atau pencatatan
  manual. Hanya produk aktif berjenis sesuai tujuan, dan isinya dibatasi per peran (AC7, AC8).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak (peran, tujuan, atau nilai yang salah).
- Edge: batas atau kasus pinggir (produk nonaktif, terhapus, isian minimal).

Produk ditulis langsung ke database, karena endpoint pendaftaran yang menerima jenis
baru menyusul di chunk validasi SCRUM-201.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from app.modules.providers import service
from app.modules.providers.models import AiProduct, MeasurementType, ProviderType
from app.modules.providers.visibility import ProductPurpose
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-0000000201ad")


def _otomatis(nama: str, **ubah) -> AiProduct:
    """Produk Otomatis yang memenuhi constraint C1 (D1): semua isian Otomatis terisi."""
    # Nilai sintetis untuk baris uji, bukan produk yang didaftarkan Veritask.
    isi = {
        "name": nama,
        "measurement_type": MeasurementType.AUTOMATIC,
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "credential_encrypted": b"ciphertext-not-the-secret",
        "credential_hint": "cret",
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("1500000.00"),
        "created_by": PEMBUAT,
    }
    isi.update(ubah)
    return AiProduct(**isi)


def _manual(nama: str, **ubah) -> AiProduct:
    """Produk Manual dengan isian wajibnya saja (C1): nama penyedia dan URL situs."""
    isi = {
        "name": nama,
        "measurement_type": MeasurementType.MANUAL,
        "vendor_name": f"Penyedia {nama}",
        "site_url": "https://contoh.test",
        "created_by": PEMBUAT,
    }
    isi.update(ubah)
    return AiProduct(**isi)


@pytest.fixture
def katalog(db_session):
    """Per jenis: dua produk aktif, satu nonaktif, satu terhapus.

    Dengan begitu setiap tes daftar sekaligus membuktikan bahwa produk nonaktif,
    terhapus, dan berjenis lain tersaring, bukan hanya bahwa produk yang benar muncul.
    """
    db_session.add_all(
        [
            _otomatis("Otomatis A"),
            _otomatis("Otomatis B", provider_type=ProviderType.GEMINI_INTERACTIONS),
            _otomatis("Otomatis Nonaktif", is_active=False),
            _otomatis("Otomatis Terhapus", deleted_at=datetime.now(UTC)),
            _manual("Manual A"),
            _manual("Manual B"),
            _manual("Manual Nonaktif", is_active=False),
            _manual("Manual Terhapus", deleted_at=datetime.now(UTC)),
        ]
    )
    db_session.commit()


def _nama(respons) -> list[str]:
    """Nama produk di respons yang berhasil, urut seperti dikirim server (urut nama)."""
    assert respons.status_code == 200, respons.text
    return [baris["name"] for baris in respons.json()]


# --- AC6: daftar lengkap untuk Admin ------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "harapan"),
    [
        ({"measurement_type": "manual"}, ["Manual A", "Manual B", "Manual Nonaktif"]),
        (
            {"measurement_type": "automatic", "is_active": "true"},
            ["Otomatis A", "Otomatis B"],
        ),
        ({"measurement_type": "automatic", "is_active": "false"}, ["Otomatis Nonaktif"]),
    ],
    ids=["manual-semua-status", "otomatis-aktif", "otomatis-nonaktif"],
)
def test_admin_menyaring_daftar_per_jenis_dan_status(as_role, katalog, params, harapan):
    """Positif: saringan jenis dan status bisa dipakai sendiri atau digabung (AC6)."""
    # Arrange
    client = as_role(Role.ADMIN)

    # Act
    respons = client.get("/admin/providers", params=params)

    # Assert
    assert _nama(respons) == harapan


def test_tanpa_saringan_admin_melihat_semua_jenis_kecuali_yang_dihapus(as_role, katalog):
    """Edge: tanpa saringan, produk terhapus tetap tidak muncul (soft delete PBI-10)."""
    # Act
    nama = _nama(as_role(Role.ADMIN).get("/admin/providers"))

    # Assert
    assert len(nama) == 6
    assert not any("Terhapus" in n for n in nama)


def test_daftar_admin_memuat_jenis_status_penyedia_dan_tanggal(as_role, katalog):
    """Positif: kolom daftar AC6 (jenis, status, nama penyedia, tanggal didaftarkan).

    Nama penyedia mengikuti D1: vendor_name untuk produk Manual, label provider_type
    untuk produk Otomatis. Tanggal didaftarkan adalah created_at.
    """
    # Act
    baris = {b["name"]: b for b in as_role(Role.ADMIN).get("/admin/providers").json()}

    # Assert
    assert baris["Manual A"]["vendor_label"] == "Penyedia Manual A"
    assert baris["Otomatis A"]["vendor_label"] == "OpenAI-compatible"
    assert baris["Otomatis B"]["vendor_label"] == "Gemini Interactions"
    assert baris["Manual Nonaktif"]["measurement_type"] == "manual"
    assert baris["Manual Nonaktif"]["is_active"] is False
    assert baris["Otomatis A"]["created_at"]


def test_saringan_jenis_yang_tidak_dikenal_ditolak(as_role, katalog):
    """Negatif: jenis di luar automatic/manual ditolak 422, bukan diabaikan diam-diam."""
    # Act
    respons = as_role(Role.ADMIN).get("/admin/providers", params={"measurement_type": "keduanya"})

    # Assert
    assert respons.status_code == 422


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.DEVELOPER_AI, Role.VIEWER])
def test_daftar_lengkap_hanya_untuk_admin(as_role, role):
    """Negatif: isian lengkap produk (URL, batas, catatan akses) hanya untuk Admin (AC8)."""
    # Act + Assert
    assert as_role(role).get("/admin/providers").status_code == 403


# --- AC7 dan AC8: daftar pilihan per tujuan ---------------------------------------------


@pytest.mark.parametrize(
    ("role", "purpose", "harapan"),
    [
        (Role.CONTRIBUTOR, "manual_entry", ["Manual A", "Manual B"]),
        (Role.ADMIN, "manual_entry", ["Manual A", "Manual B"]),
        (Role.ADMIN, "automatic_run", ["Otomatis A", "Otomatis B"]),
        (Role.DEVELOPER_AI, "automatic_run", ["Otomatis A", "Otomatis B"]),
    ],
)
def test_daftar_pilihan_hanya_produk_aktif_berjenis_sesuai(
    as_role, katalog, role, purpose, harapan
):
    """Positif dan edge: setiap peran yang berhak mendapat produk aktif berjenis sesuai.

    Produk nonaktif, terhapus, atau berjenis lain tidak dikirim sama sekali, bukan
    sekadar ditandai (AC7, D1 C4).
    """
    # Act
    respons = as_role(role).get("/ai-products", params={"purpose": purpose})

    # Assert
    assert _nama(respons) == harapan


def test_kontributor_hanya_menerima_nama_produk_manual(as_role, katalog):
    """Edge: isian pilihan dibatasi ke id, nama, dan jenis (AC8).

    Kontributor tidak boleh melihat URL situs, catatan akses, parameter, atau
    isian Otomatis, jadi yang diuji adalah himpunan kunci di setiap baris.
    """
    # Act
    respons = as_role(Role.CONTRIBUTOR).get("/ai-products", params={"purpose": "manual_entry"})

    # Assert
    assert {tuple(sorted(baris)) for baris in respons.json()} == {
        ("id", "measurement_type", "name")
    }


def test_daftar_pilihan_tidak_membaca_kredensial_dari_database(as_role, katalog, db_session):
    """Negatif (pengaman, review #81 poin 5): query daftar pilihan hanya memilih kolom
    id, nama, dan jenis. Ciphertext kredensial dan catatan akses tidak pernah terbaca
    untuk permintaan Kontributor, bukan sekadar disaring sebelum respons dikirim."""
    # Arrange
    kontributor = as_role(Role.CONTRIBUTOR)
    perintah: list[str] = []

    def catat(_conn, _cursor, statement, *_args):
        perintah.append(statement)

    mesin = db_session.get_bind()
    event.listen(mesin, "before_cursor_execute", catat)

    # Act
    try:
        respons = kontributor.get("/ai-products", params={"purpose": "manual_entry"})
    finally:
        event.remove(mesin, "before_cursor_execute", catat)

    # Assert
    assert _nama(respons) == ["Manual A", "Manual B"]
    query_produk = [sql for sql in perintah if "FROM ai_products" in sql]
    assert query_produk
    for sql in query_produk:
        assert "credential_encrypted" not in sql and "access_note" not in sql


@pytest.mark.parametrize(
    ("role", "purpose"),
    [
        (Role.CONTRIBUTOR, "automatic_run"),
        (Role.DEVELOPER_AI, "manual_entry"),
        (Role.VIEWER, "manual_entry"),
        (Role.VIEWER, "automatic_run"),
    ],
)
def test_tujuan_di_luar_peran_ditolak(as_role, katalog, role, purpose):
    """Negatif: peran yang tidak berhak atas tujuan itu ditolak 403 (matriks D10)."""
    # Act
    respons = as_role(role).get("/ai-products", params={"purpose": purpose})

    # Assert
    assert respons.status_code == 403


@pytest.mark.parametrize(
    "params", [{}, {"purpose": "laporan"}], ids=["tanpa-tujuan", "tidak-dikenal"]
)
def test_tujuan_wajib_dan_harus_dikenal(as_role, katalog, params):
    """Negatif: tanpa tujuan yang jelas server tidak bisa memilih jenis, jadi 422."""
    # Act + Assert
    assert as_role(Role.ADMIN).get("/ai-products", params=params).status_code == 422


def test_daftar_pilihan_tanpa_login_401(client):
    """Negatif: daftar pilihan tidak terbuka untuk pengguna yang belum login."""
    # Act + Assert
    assert client.get("/ai-products", params={"purpose": "manual_entry"}).status_code == 401


# --- Service dengan repository di-mock ---------------------------------------------------
# Tes di bawah tidak memakai database: repository diganti MagicMock supaya yang diuji
# hanya keputusan service (query apa yang diminta, dan kapan query tidak boleh terjadi).


def test_service_meminta_produk_aktif_berjenis_sesuai_tujuan(monkeypatch):
    """Positif: tujuan manual_entry diterjemahkan ke query produk aktif berjenis manual."""
    # Arrange
    db = MagicMock()
    repo = MagicMock()
    repo.list_options.return_value = [_manual("Manual A", id=uuid.uuid4())]
    monkeypatch.setattr(service, "repository", repo)

    # Act
    hasil = service.list_options(db, purpose=ProductPurpose.MANUAL_ENTRY, role=Role.CONTRIBUTOR)

    # Assert
    repo.list_options.assert_called_once_with(db, measurement_type=MeasurementType.MANUAL)
    assert [p.name for p in hasil] == ["Manual A"]


def test_service_menolak_sebelum_menyentuh_database(monkeypatch):
    """Negatif: peran yang tidak berhak ditolak sebelum query apa pun dijalankan."""
    # Arrange
    repo = MagicMock()
    monkeypatch.setattr(service, "repository", repo)

    # Act
    with pytest.raises(ForbiddenError):
        service.list_options(
            MagicMock(), purpose=ProductPurpose.AUTOMATIC_RUN, role=Role.CONTRIBUTOR
        )

    # Assert
    repo.list_options.assert_not_called()
