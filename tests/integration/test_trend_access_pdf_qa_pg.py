"""QA hak akses tren dan PDF tren lewat API mentah. PBI-17 (SCRUM-84) AC1, AC7, AC8, AC12, AC13;
SCRUM-336.

Matriks peran x jenis kasus diuji tanpa bantuan UI: filter yang disembunyikan klien tidak
dihitung. PDF dibuat pembuat asli tanpa kompresi, jadi teksnya bisa dicari di berkasnya
(pola test_trend_pdf_api.py), dan isinya dibandingkan dengan GET /reports/trend dengan pilihan
yang sama.

Label: Positif = boleh, Negatif = ditolak, Edge = batas perilaku.
"""

import re
from decimal import Decimal

import pytest

from app.main import app
from app.modules.reports.dependencies import get_trend_pdf_renderer
from app.modules.reports.trend_pdf import TrendPdfRenderer
from app.shared.security import get_current_user
from tests.trend_qa_pg import (
    ADMIN,
    DEV_AI,
    HANYA_POSTGRES,
    KONTRIBUTOR,
    SEMUA,
    VIEWER,
    ProviderTren,
    sesi_manual,
    sesi_otomatis,
    titik,
    tren,
    waktu_di_bulan,
)

pytestmark = HANYA_POSTGRES

LINGKUP = ("test", "dev", "dev_test")
BOLEH_DEV = {ADMIN, DEV_AI}


@pytest.fixture
def dua_bulan(dunia_tren):
    """AiYU diukur bulan lalu (jawaban sedang) dan bulan ini (jawaban baik), semua kasus."""
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(1), provider=ProviderTren("sedang"))
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(0), provider=ProviderTren("baik"))
    app.dependency_overrides[get_trend_pdf_renderer] = lambda: TrendPdfRenderer(compress=False)
    return dunia_tren


def _teks(isi: str) -> bytes:
    """Teks yang dicetak pembuat PDF tanpa kompresi (reportlab menulis `(teks) Tj`)."""
    return b"(" + isi.encode() + b") Tj"


def test_matriks_peran_dan_jenis_kasus_lewat_api_mentah(dua_bulan):
    """Positif dan Negatif: AC1 dan AC12, semua peran membaca tren test; tren dev dan gabungan
    hanya untuk Admin dan Developer AI. Viewer dan Kontributor mendapat 403 tanpa satu pun
    titik atau skor di badan respons."""
    # Act
    hasil = {
        (o, lingkup): tren(dua_bulan, "AiYU", o, score_scope=lingkup)
        for o in SEMUA
        for lingkup in LINGKUP
    }

    # Assert
    harapan = {
        (o.nama, lingkup): 200 if lingkup == "test" or o in BOLEH_DEV else 403
        for o in SEMUA
        for lingkup in LINGKUP
    }
    assert {(o.nama, lingkup): r.status_code for (o, lingkup), r in hasil.items()} == harapan
    for (_orang, lingkup), respons in hasil.items():
        if respons.status_code == 403:
            assert respons.json()["code"] == "TREND_SCOPE_FORBIDDEN"
            assert "points" not in respons.text and "1.00" not in respons.text
        else:
            assert respons.json()["score_scope"] == lingkup
            assert len(titik(respons)) == 2


def test_tanpa_pilihan_jenis_kasus_semua_peran_mendapat_tren_test(dua_bulan):
    """Positif: AC12, pilihan bawaan adalah Test only untuk setiap peran, termasuk Admin dan
    Developer AI yang boleh memilih dev."""
    # Act
    lingkup = {o.nama: tren(dua_bulan, "AiYU", o).json()["score_scope"] for o in SEMUA}

    # Assert
    assert lingkup == {o.nama: "test" for o in SEMUA}


def test_tren_dev_bisa_diminta_untuk_produk_ai_mana_pun(dunia_tren):
    """Positif: AC13, Developer AI meminta tren dev untuk produk otomatis lain dan produk manual,
    bukan hanya untuk satu produk milik timnya."""
    # Arrange
    sesi_otomatis(dunia_tren, "Justisio", kapan=waktu_di_bulan(0))
    sesi_manual(dunia_tren, "LegalHero", kapan=waktu_di_bulan(0))

    # Act
    respons = {
        nama: tren(dunia_tren, nama, DEV_AI, score_scope="dev")
        for nama in ("Justisio", "LegalHero")
    }

    # Assert
    assert {nama: [p["value"] for p in titik(r)] for nama, r in respons.items()} == {
        "Justisio": ["1.00"],
        "LegalHero": ["1.00"],
    }


def test_tanpa_login_tren_dan_pdf_ditolak_401(dua_bulan):
    """Negatif: tren dan PDF-nya hanya untuk pengguna login, tidak ada tautan publik."""
    # Arrange
    produk = str(dua_bulan.produk["AiYU"].id)
    app.dependency_overrides.pop(get_current_user)

    # Act
    status = {
        jalur: dua_bulan.client.get(jalur, params={"product_id": produk}).status_code
        for jalur in ("/reports/trend", "/reports/trend/pdf")
    }

    # Assert
    assert status == {"/reports/trend": 401, "/reports/trend/pdf": 401}


def test_pdf_berisi_titik_yang_sama_dengan_halaman_tanpa_garis_target(dua_bulan):
    """Positif: AC8 dan AC7, PDF dari pilihan yang sama dengan halaman memuat nilai setiap
    titik yang sama, sebagai lampiran PDF, dengan catatan tanpa garis target."""
    # Arrange
    halaman = titik(tren(dua_bulan, "AiYU", metric="llm"))

    # Act
    respons = tren(dua_bulan, "AiYU", pdf=True, metric="llm")

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.headers["content-type"] == "application/pdf"
    assert respons.content.startswith(b"%PDF")
    produk = dua_bulan.produk["AiYU"].id
    assert re.fullmatch(
        rf'attachment; filename="tren-{produk}-test-llm-all-en-\d{{4}}-\d{{2}}\.pdf"',
        respons.headers["content-disposition"],
    )
    assert [p["value"] for p in halaman] == ["0.50", "1.00"]
    for nilai in ("0.50", "1.00"):
        assert _teks(nilai) in respons.content
    assert _teks("No target line.") in respons.content
    assert _teks("Score trend: AiYU") in respons.content


def test_pdf_berbahasa_indonesia_memakai_koma_desimal(dua_bulan):
    """Edge: AC8, language=id mencetak judul, catatan tanpa garis target, dan skor dengan koma."""
    # Act
    respons = tren(dua_bulan, "AiYU", pdf=True, metric="llm", language="id")

    # Assert
    assert respons.status_code == 200, respons.text
    assert _teks("Tren skor AiYU") in respons.content
    assert _teks("Tanpa garis target.") in respons.content
    assert _teks(str(Decimal("0.50")).replace(".", ",")) in respons.content


@pytest.mark.parametrize(
    ("orang", "lingkup", "status", "spanduk_dev"),
    [
        # Positif: Admin mengunduh PDF dev; PDF-nya diberi spanduk skor dev internal.
        (ADMIN, "dev", 200, True),
        # Positif: PDF test tanpa spanduk dev.
        (ADMIN, "test", 200, False),
        # Positif: Developer AI boleh PDF gabungan (AC12).
        (DEV_AI, "dev_test", 200, True),
        # Negatif: Viewer dan Kontributor tidak bisa mengunduh PDF dev lewat API mentah.
        (VIEWER, "dev", 403, False),
        (KONTRIBUTOR, "dev_test", 403, False),
    ],
)
def test_pdf_mengikuti_hak_peran_atas_skor_dev(dua_bulan, orang, lingkup, status, spanduk_dev):
    """Positif dan Negatif: AC8 dan AC12, aturan jenis kasus PDF sama dengan halaman tren."""
    # Act
    respons = tren(dua_bulan, "AiYU", orang, pdf=True, score_scope=lingkup)

    # Assert
    assert respons.status_code == status
    spanduk = _teks("This trend contains dev scores. Internal only, it is never published.")
    assert (spanduk in respons.content) is spanduk_dev
    if status == 403:
        assert respons.json()["code"] == "TREND_SCOPE_FORBIDDEN"
