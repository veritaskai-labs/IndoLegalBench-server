"""Unit test aturan baca laporan tersimpan (PBI-15 SCRUM-320, matriks peran D9).

Aturan murni, tanpa database: satu objek ReportVisibility per pengguna dipakai untuk
menyaring daftar laporan (repository) dan membuka satu laporan (service), jadi keduanya
tidak bisa berbeda.

Sumber aturan:
- D9 "Membuka laporan tersimpan milik orang lain": hanya Admin (keputusan SA).
- D9 "Daftar laporan terbit": semua pengguna login. Versi lama (superseded) tetap
  tersimpan dan tetap bisa dibuka (PBI-17 AC penerbitan).
- D9 "Laporan dengan skor dev": hanya Admin dan Developer AI. Skor dev tidak pernah
  dikirim ke Viewer dan Kontributor (D9 aturan umum 3).

Label: Positif = boleh dibaca, Negatif = harus disembunyikan, Edge = batas aturan.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.modules.cases.models import SplitTag
from app.modules.cases.service import MASKED_CASE_CODE
from app.modules.reports import access
from app.modules.reports.access import ReportVisibility, visibility_for, visible_case_code
from app.modules.reports.models import ProductLabelMode, ReportStatus
from app.shared.security import CurrentUser, Role

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


def _pengguna(role: Role, user_id: uuid.UUID = PEMBUAT) -> CurrentUser:
    return CurrentUser(user_id=user_id, name="Uji", email="uji@veritask.test", role=role)


def _laporan(status=ReportStatus.SAVED, created_by=PEMBUAT, has_dev_scores=False):
    """Cukup tiga atribut yang dibaca aturan, jadi tidak perlu baris ORM."""
    return SimpleNamespace(status=status, created_by=created_by, has_dev_scores=has_dev_scores)


@pytest.mark.parametrize(
    ("role", "all_saved", "include_dev"),
    [
        # Positif: Admin melihat semua laporan tersimpan dan skor dev.
        (Role.ADMIN, True, True),
        # Positif: Developer AI boleh skor dev, tetapi hanya laporan tersimpan miliknya.
        (Role.DEVELOPER_AI, False, True),
        # Negatif: Viewer dan Kontributor tidak pernah menerima skor dev.
        (Role.VIEWER, False, False),
        (Role.CONTRIBUTOR, False, False),
    ],
)
def test_visibility_for_mengikuti_matriks_d9(role, all_saved, include_dev):
    """Positif dan negatif: hak per peran diterjemahkan sekali menjadi tiga bendera filter."""
    # Act
    visibility = visibility_for(_pengguna(role))

    # Assert
    assert visibility == ReportVisibility(
        viewer_id=PEMBUAT, all_saved=all_saved, include_dev=include_dev
    )


@pytest.mark.parametrize("role", list(Role))
def test_pembuat_selalu_bisa_membuka_laporan_tersimpan_miliknya(role):
    """Positif: laporan otomatis menjadi riwayat pembuatnya (AC riwayat laporan)."""
    # Act
    boleh = visibility_for(_pengguna(role)).allows(_laporan())

    # Assert
    assert boleh is True


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR, Role.DEVELOPER_AI])
def test_laporan_tersimpan_orang_lain_tertutup_selain_admin(role):
    """Negatif: laporan belum terbit milik orang lain hanya untuk Admin (keputusan SA D9)."""
    # Act
    boleh = visibility_for(_pengguna(role)).allows(_laporan(created_by=ORANG_LAIN))

    # Assert
    assert boleh is False


def test_admin_membuka_laporan_tersimpan_milik_siapa_pun():
    """Positif: Admin perlu membuka laporan tersimpan orang lain untuk menerbitkannya."""
    # Act
    boleh = visibility_for(_pengguna(Role.ADMIN)).allows(_laporan(created_by=ORANG_LAIN))

    # Assert
    assert boleh is True


@pytest.mark.parametrize("status", [ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED])
@pytest.mark.parametrize("role", list(Role))
def test_laporan_terbit_dan_versi_lamanya_terbuka_untuk_semua_peran(role, status):
    """Positif: laporan terbit tampil untuk semua pengguna login; versi lama tetap tersimpan."""
    # Act
    boleh = visibility_for(_pengguna(role)).allows(_laporan(status=status, created_by=ORANG_LAIN))

    # Assert
    assert boleh is True


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
def test_laporan_skor_dev_tertutup_walau_milik_sendiri(role):
    """Edge: data lama atau rusak sekalipun, skor dev tidak pernah sampai ke Viewer/Kontributor."""
    # Act
    boleh = visibility_for(_pengguna(role)).allows(_laporan(has_dev_scores=True))

    # Assert
    assert boleh is False


def test_developer_ai_membuka_laporan_skor_dev_miliknya():
    """Positif: Developer AI membandingkan kasus dev antar produk (AC laporan perbandingan dev)."""
    # Act
    boleh = visibility_for(_pengguna(Role.DEVELOPER_AI)).allows(_laporan(has_dev_scores=True))

    # Assert
    assert boleh is True


@pytest.mark.parametrize(
    ("role", "split_tag", "kode"),
    [
        # Negatif: Developer AI tidak melihat kode kasus test (D9 aturan umum 2, SCRUM-183).
        (Role.DEVELOPER_AI, SplitTag.TEST, MASKED_CASE_CODE),
        # Positif: kasus dev terbuka untuk Developer AI.
        (Role.DEVELOPER_AI, SplitTag.DEV, "UAT-PHK-001"),
        # Positif: Viewer dan Kontributor melihat kode tanpa isi (D9 penelusuran tanpa isi).
        (Role.VIEWER, SplitTag.TEST, "UAT-PHK-001"),
        (Role.CONTRIBUTOR, SplitTag.TEST, "UAT-PHK-001"),
        (Role.ADMIN, SplitTag.TEST, "UAT-PHK-001"),
    ],
)
def test_kode_kasus_dasar_disamarkan_hanya_untuk_developer_ai(role, split_tag, kode):
    """Positif dan negatif: penyamaran kasus test berlaku juga di daftar kasus dasar laporan."""
    # Act
    hasil = visible_case_code(role, "UAT-PHK-001", split_tag)

    # Assert
    assert hasil == kode


@pytest.mark.parametrize(
    ("role", "mode", "boleh"),
    [
        # Positif: hanya Admin yang menerima pemetaan kode ke nama asli (AC9).
        (Role.ADMIN, ProductLabelMode.CODE, True),
        # Negatif: peran lain tidak pernah menerimanya, termasuk Developer AI.
        (Role.DEVELOPER_AI, ProductLabelMode.CODE, False),
        (Role.CONTRIBUTOR, ProductLabelMode.CODE, False),
        (Role.VIEWER, ProductLabelMode.CODE, False),
        # Edge: mode nama asli tidak punya kode untuk dipetakan, jadi Admin pun tidak menerimanya.
        (Role.ADMIN, ProductLabelMode.REAL_NAME, False),
        (Role.VIEWER, ProductLabelMode.REAL_NAME, False),
    ],
)
def test_pemetaan_produk_hanya_untuk_admin_di_mode_kode(role, mode, boleh):
    """Positif dan negatif: aturan pemetaan label produk dijaga satu tempat di server."""
    # Act
    hasil = access.sees_product_mapping(role, mode)

    # Assert
    assert hasil is boleh
