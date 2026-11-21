"""Unit test pembuat PDF tren (PBI-17 SCRUM-84 SCRUM-333).

Fungsi murni: TrendRead (response GET /reports/trend) masuk, bytes PDF keluar. Tidak ada
database dan HTTP, jadi tidak ada yang perlu di-mock. Waktu pembuatan dikirim sebagai
argumen (jam palsu), supaya tanggal di PDF dan nama berkas selalu sama.

Isi PDF dibaca dengan merender tanpa kompresi lalu mencari teks di bytes-nya, sama dengan
test_report_pdf.py (tidak perlu library pembaca PDF baru). Baris tabel dan grafik
diperiksa dari fungsi pembentuknya, karena angka sumbu grafik juga berbentuk "0,00".

Label: Positif = jalur normal, Negatif = data yang harus ditolak atau disembunyikan,
Edge = batas atau data tidak lengkap.
"""

import base64
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Line

from app.modules.reports.comparison_rules import (
    JUDGE_MODEL,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    RUBRIC_VERSION,
    SUITE_VERSION,
    TRIAL_SESSION,
)
from app.modules.reports.models import ReportLanguage, ScoreScope
from app.modules.reports.schemas import TrendPointRead, TrendRead
from app.modules.reports.trend import TrendMetric, TrendRange
from app.modules.reports.trend_pdf import (
    TrendPdfRenderer,
    _baris_tren,
    _grafik_garis,
    trend_pdf_filename,
)

PRODUK = uuid.UUID("00000000-0000-0000-0000-0000000000b7")
# 21 Nov 2026 10:00 WIB.
DIBUAT = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _titik(bulan: int, nilai: str | None = "0.83", tahun: int = 2026, **ubah) -> TrendPointRead:
    isi = {
        "month": date(tahun, bulan, 1),
        "label": f"Sesi bulan {bulan}",
        "value": None if nilai is None else Decimal(nilai),
        "breaks": [],
        "markers": [],
        "case_count": 2,
    }
    isi.update(ubah)
    return TrendPointRead(**isi)


def _tren(points=None, **ubah) -> TrendRead:
    isi = {
        "product_id": PRODUK,
        "product_name": "Produk A",
        "score_scope": ScoreScope.TEST,
        "metric": TrendMetric.TOTAL,
        "range": TrendRange.ALL,
        "points": points if points is not None else [_titik(10), _titik(11, "0.61")],
        "warnings": [],
    }
    isi.update(ubah)
    return TrendRead(**isi)


def _pdf(tren: TrendRead, language=ReportLanguage.ID, generated_at=DIBUAT, **opsi) -> bytes:
    return TrendPdfRenderer(compress=False, **opsi).render(
        tren, language=language, generated_at=generated_at
    )


def _halaman(isi: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", isi))


def _sambung(isi: bytes) -> bytes:
    """Gabungkan potongan teks satu baris. Reportlab menulis teks hasil escape() sebagai
    beberapa operator Tj berurutan, misalnya (Sesi <) Tj (i) Tj (>) Tj."""
    return isi.replace(b") Tj (", b"")


# --- Bagian atas ---------------------------------------------------------------


class TestHeader:
    def test_pdf_memuat_judul_produk_metrik_jenis_kasus_rentang_dan_tanggal(self):
        """Positif: PDF menjelaskan apa yang digambar tanpa harus membuka halaman web (AC8)."""
        # Act
        isi = _pdf(_tren())

        # Assert
        assert isi.startswith(b"%PDF")
        assert b"Tren skor Produk A" in isi
        assert b"Metrik: Skor total" in isi
        assert b"Jenis kasus: Kasus test" in isi
        assert b"Rentang: Semua sesi" in isi
        assert b"Dibuat 21 November 2026" in isi

    def test_tanggal_dibuat_memakai_wib_bukan_utc(self):
        """Edge: 30 Nov 18.00 UTC sudah 1 Desember di WIB."""
        # Act
        isi = _pdf(_tren(), generated_at=datetime(2026, 11, 30, 18, 0, tzinfo=UTC))

        # Assert
        assert b"Dibuat 1 Desember 2026" in isi
        assert b"Dibuat 30 November 2026" not in isi

    def test_bahasa_inggris_memakai_label_dan_titik_desimal_inggris(self):
        """Positif: bahasa disiapkan untuk PBI-26 (SCRUM-355); bawaan endpoint adalah Inggris."""
        # Act
        isi = _pdf(_tren(), language=ReportLanguage.EN)

        # Assert
        assert b"Score trend: Produk A" in isi
        assert b"Metric: Total score" in isi
        assert b"Case type: Test cases" in isi
        assert b"Range: All sessions" in isi
        assert b"Created 21 November 2026" in isi
        assert b"0.83" in isi
        assert b"Tren skor" not in isi

    @pytest.mark.parametrize(
        ("metric", "id_teks", "en_teks"),
        [
            (TrendMetric.TOTAL, b"Skor total", b"Total score"),
            (TrendMetric.DETERMINISTIC, b"Skor deterministik", b"Deterministic score"),
            (TrendMetric.LLM, b"Skor penilai LLM", b"LLM judge score"),
        ],
    )
    def test_nama_metrik_sesuai_pilihan(self, metric, id_teks, en_teks):
        """Positif: satu garis untuk satu metrik, dan namanya tercetak (AC3)."""
        # Act
        isi_id = _pdf(_tren(metric=metric))
        isi_en = _pdf(_tren(metric=metric), language=ReportLanguage.EN)

        # Assert
        assert id_teks in isi_id
        assert en_teks in isi_en

    @pytest.mark.parametrize(
        ("scope", "teks"),
        [
            (ScoreScope.TEST, b"Kasus test"),
            (ScoreScope.DEV, b"Kasus dev"),
            (ScoreScope.DEV_TEST, b"Gabungan dev + test"),
        ],
    )
    def test_jenis_kasus_sesuai_filter(self, scope, teks):
        """Positif: filter jenis kasus ikut tercetak (AC12)."""
        # Act
        isi = _pdf(_tren(score_scope=scope))

        # Assert
        assert b"Jenis kasus: " + teks in isi

    @pytest.mark.parametrize(
        ("rentang", "teks"),
        [
            (TrendRange.ALL, b"Semua sesi"),
            (TrendRange.THREE_MONTHS, b"3 bulan terakhir"),
            (TrendRange.SIX_MONTHS, b"6 bulan terakhir"),
            (TrendRange.TWELVE_MONTHS, b"12 bulan terakhir"),
        ],
    )
    def test_rentang_sesuai_filter(self, rentang, teks):
        """Positif: rentang 3, 6, 12 bulan atau semua (AC2)."""
        # Act
        isi = _pdf(_tren(range=rentang))

        # Assert
        assert b"Rentang: " + teks in isi

    def test_pdf_memakai_berkas_logo_bila_tersedia(self, tmp_path):
        """Positif: logo Veritask sama dengan PDF laporan (pembuat PDF yang sama)."""
        # Arrange
        logo = tmp_path / "logo.png"
        logo.write_bytes(PNG_1X1)

        # Act
        isi = _pdf(_tren(), logo_path=logo)

        # Assert
        assert b"/Subtype /Image" in isi

    def test_logo_bawaan_adalah_logo_resmi_di_repo(self):
        """Positif: tanpa opsi, logo resmi di app/modules/reports/assets yang dipakai."""
        # Act
        isi = TrendPdfRenderer(compress=False).render(
            _tren(), language=ReportLanguage.ID, generated_at=DIBUAT
        )

        # Assert
        assert b"/Subtype /Image" in isi

    def test_tanpa_berkas_logo_memakai_wordmark(self, tmp_path):
        """Edge: berkas logo hilang, PDF tetap jadi dengan tulisan VERITASK."""
        # Act
        isi = _pdf(_tren(), logo_path=tmp_path / "tidak-ada.png")

        # Assert
        assert b"/Subtype /Image" not in isi
        assert b"VERITASK" in isi

    def test_teks_pengguna_mirip_markup_tidak_merusak_file(self):
        """Negatif: nama produk dan nama sesi diketik pengguna; Paragraph membaca tag XML."""
        # Arrange
        tren = _tren(
            product_name="<b>A&B</b> <x>",
            points=[_titik(10, label="Sesi <i>Okt</i> & <y>")],
        )

        # Act
        isi = _pdf(tren)

        # Assert
        assert isi.startswith(b"%PDF")
        assert b"<b>A&B</b> <x>" in _sambung(isi)
        assert b"Sesi <i>Okt</i> & <y>" in _sambung(isi)

    def test_pdf_memuat_teks_permintaan_isi_kasus_lewat_email(self):
        """Positif: bagian bawah sama dengan PDF laporan."""
        # Act
        isi = _pdf(_tren())

        # Assert
        assert b"indolegalbench@veritask.ai" in isi


# --- Tabel per bulan -----------------------------------------------------------


class TestTable:
    def test_satu_baris_per_titik_dengan_bulan_sesi_nilai_dan_jumlah_kasus(self):
        """Positif: tiap titik tren menjadi satu baris, urut dari bulan paling awal (AC4)."""
        # Act
        baris = _baris_tren(_tren(), ReportLanguage.ID)

        # Assert
        assert baris == [
            ["Oktober 2026", "Sesi bulan 10", "0,83", "2", "", ""],
            ["November 2026", "Sesi bulan 11", "0,61", "2", "", ""],
        ]

    def test_bahasa_inggris_memakai_nama_bulan_dan_titik_desimal_inggris(self):
        """Positif: format angka mengikuti bahasa, sama dengan format_score PDF laporan."""
        # Act
        baris = _baris_tren(_tren(), ReportLanguage.EN)

        # Assert
        assert [b[:3] for b in baris] == [
            ["October 2026", "Sesi bulan 10", "0.83"],
            ["November 2026", "Sesi bulan 11", "0.61"],
        ]

    def test_nilai_kosong_dicetak_strip_bukan_nol(self):
        """Negatif: titik tanpa nilai (tidak ada kasus sama, skor jalur kosong) bukan skor 0."""
        # Act
        baris = _baris_tren(_tren(points=[_titik(10, None, case_count=0)]), ReportLanguage.ID)

        # Assert
        assert baris[0][2] == "–"
        assert baris[0][3] == "0"

    def test_nilai_dua_desimal_tanpa_persen(self):
        """Positif: skor 0 sampai 1 dua desimal, tanpa persen (sama dengan laporan)."""
        # Act
        baris = _baris_tren(_tren(points=[_titik(10, "1.00"), _titik(11, "0")]), ReportLanguage.ID)

        # Assert
        assert [b[2] for b in baris] == ["1,00", "0,00"]

    def test_nilai_dan_jumlah_kasus_tercetak_di_pdf(self):
        """Positif: isi baris benar-benar masuk ke berkas, bukan hanya ke fungsi pembentuknya."""
        # Act
        isi = _pdf(_tren(points=[_titik(10, "0.83", case_count=17)]))

        # Assert
        for teks in (b"Oktober 2026", b"Sesi bulan 10", b"0,83", b"17"):
            assert teks in isi

    def test_tren_kosong_tetap_jadi_pdf_dengan_keterangan(self):
        """Edge: produk tanpa sesi layak di rentang ini, PDF tetap bisa diunduh."""
        # Act
        isi = _pdf(_tren(points=[]))

        # Assert
        assert isi.startswith(b"%PDF")
        assert b"Belum ada sesi layak untuk produk ini pada rentang ini." in isi
        assert _baris_tren(_tren(points=[]), ReportLanguage.ID) == []

    def test_tren_kosong_dalam_bahasa_inggris(self):
        """Edge: keterangan kosong juga punya versi Inggris."""
        # Act
        isi = _pdf(_tren(points=[]), language=ReportLanguage.EN)

        # Assert
        assert b"No eligible session for this product in this range." in isi

    def test_dua_belas_titik_penuh(self):
        """Edge: batas kapasitas PRD, 12 titik per garis pada rentang 12 bulan."""
        # Arrange
        titik = [_titik(bulan, tahun=2026) for bulan in range(1, 13)]

        # Act
        isi = _pdf(_tren(points=titik, range=TrendRange.TWELVE_MONTHS))

        # Assert
        assert isi.startswith(b"%PDF")
        assert len(_baris_tren(_tren(points=titik), ReportLanguage.ID)) == 12
        assert b"Januari 2026" in isi
        assert b"Desember 2026" in isi

    def test_rentang_semua_lebih_dari_dua_belas_titik_berlanjut_ke_halaman_berikut(self):
        """Edge: 'semua' bisa melebihi 12 titik; tabel pindah halaman tanpa terpotong."""
        # Arrange
        titik = [
            _titik(bulan, tahun=tahun) for tahun in (2024, 2025, 2026) for bulan in range(1, 13)
        ]

        # Act
        isi = _pdf(_tren(points=titik))

        # Assert
        assert _halaman(isi) > 1
        assert b"Januari 2024" in isi
        assert b"Desember 2026" in isi


# --- Alasan putus, penanda, peringatan ------------------------------------------

ALASAN_PUTUS = {
    RUBRIC_VERSION: ("Versi rubrik berubah", "Rubric version changed"),
    JUDGE_MODEL: ("Model penilai berubah", "Judge model changed"),
    SUITE_VERSION: ("Versi suite atau kasus berubah", "Suite or case version changed"),
    MODEL_VERSION_DIFFERS: ("Versi model produk berubah", "Product model version changed"),
}
PENANDA = {
    TRIAL_SESSION: ("Uji Coba", "Trial"),
    REPETITIONS_DIFFER: ("Jumlah percobaan berbeda", "Different number of attempts"),
    MANUAL_ANSWER_RANGE_OVER_LIMIT: (
        "Rentang jawaban manual lebih dari 7 hari",
        "Manual answers span more than 7 days",
    ),
}


class TestBreaksMarkersWarnings:
    @pytest.mark.parametrize("kode", list(ALASAN_PUTUS))
    @pytest.mark.parametrize("bahasa", [ReportLanguage.ID, ReportLanguage.EN])
    def test_setiap_alasan_putus_punya_label_di_kedua_bahasa(self, kode, bahasa):
        """Positif: garis putus ditandai jelas, bukan kode mentah (AC5)."""
        # Arrange
        tren = _tren(points=[_titik(10), _titik(11, breaks=[kode])])

        # Act
        baris = _baris_tren(tren, bahasa)

        # Assert
        assert baris[0][4] == ""
        assert baris[1][4] == ALASAN_PUTUS[kode][bahasa is ReportLanguage.EN]

    @pytest.mark.parametrize("kode", list(PENANDA))
    @pytest.mark.parametrize("bahasa", [ReportLanguage.ID, ReportLanguage.EN])
    def test_setiap_penanda_punya_label_di_kedua_bahasa(self, kode, bahasa):
        """Positif: Uji Coba, jumlah percobaan berbeda, rentang jawaban manual (AC5)."""
        # Arrange
        tren = _tren(points=[_titik(10, markers=[kode])])

        # Act
        baris = _baris_tren(tren, bahasa)

        # Assert
        assert baris[0][5] == PENANDA[kode][bahasa is ReportLanguage.EN]

    def test_beberapa_alasan_dan_penanda_digabung_dalam_satu_sel(self):
        """Positif: semua alasan putus tercetak, sama dengan daftar breaks dari tren."""
        # Arrange
        tren = _tren(
            points=[
                _titik(
                    10,
                    breaks=[RUBRIC_VERSION, MODEL_VERSION_DIFFERS],
                    markers=[TRIAL_SESSION, REPETITIONS_DIFFER],
                )
            ]
        )

        # Act
        baris = _baris_tren(tren, ReportLanguage.ID)

        # Assert
        assert baris[0][4] == "Versi rubrik berubah; Versi model produk berubah"
        assert baris[0][5] == "Uji Coba; Jumlah percobaan berbeda"

    def test_kode_yang_belum_dikenal_tetap_tercetak_apa_adanya(self):
        """Edge: kode baru dari service tren tidak membuat PDF gagal dan tidak hilang diam-diam."""
        # Arrange
        tren = _tren(points=[_titik(10, breaks=["KODE_BARU"], markers=["<PENANDA&BARU>"])])

        # Act
        baris = _baris_tren(tren, ReportLanguage.ID)
        isi = _pdf(tren)

        # Assert
        assert baris[0][4:] == ["KODE_BARU", "<PENANDA&BARU>"]
        assert b"<PENANDA&BARU>" in _sambung(isi)

    def test_alasan_putus_dan_penanda_masuk_ke_berkas(self):
        """Positif: label benar-benar tercetak di PDF."""
        # Arrange
        tren = _tren(
            points=[_titik(10), _titik(11, breaks=[RUBRIC_VERSION], markers=[TRIAL_SESSION])]
        )

        # Act
        isi = _pdf(tren)

        # Assert
        assert b"Versi rubrik berubah" in isi
        assert b"Uji Coba" in isi

    @pytest.mark.parametrize(
        ("bahasa", "teks"),
        [
            (
                ReportLanguage.ID,
                b"Tidak ada kasus yang sama di antara sesi pada satu garis, jadi garis itu tidak digambar.",
            ),
            (
                ReportLanguage.EN,
                b"No case is shared by the sessions of a line, so that line is not drawn.",
            ),
        ],
    )
    def test_peringatan_tanpa_kasus_sama_tercetak(self, bahasa, teks):
        """Edge: tidak ada kasus yang sama, peringatan jelas tanpa garis (AC11)."""
        # Arrange
        tren = _tren(
            points=[_titik(10, None, case_count=0), _titik(11, None, case_count=0)],
            warnings=[NO_SHARED_CASES],
        )

        # Act
        isi = _pdf(tren, language=bahasa)

        # Assert
        assert teks in isi

    def test_tanpa_peringatan_tidak_ada_teks_peringatan(self):
        """Negatif: tren normal tidak diberi peringatan palsu."""
        # Act
        isi = _pdf(_tren())

        # Assert
        assert b"Tidak ada kasus yang sama" not in isi

    def test_peringatan_yang_belum_dikenal_tetap_tercetak(self):
        """Edge: kode peringatan baru tetap tercetak apa adanya."""
        # Act
        isi = _pdf(_tren(warnings=["PERINGATAN_BARU"]))

        # Assert
        assert b"PERINGATAN_BARU" in isi

    def test_catatan_tanpa_garis_target_tercetak(self):
        """Positif: tidak ada garis target OKR/KR3c, dan pembaca diberi tahu (AC7)."""
        # Act
        isi_id = _pdf(_tren())
        isi_en = _pdf(_tren(), language=ReportLanguage.EN)

        # Assert
        assert b"Tanpa garis target." in isi_id
        assert b"No target line." in isi_en


class TestDevBanner:
    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_tren_dengan_skor_dev_diberi_tanda_internal(self, scope):
        """Positif: tren dev hanya untuk internal dan tidak pernah diterbitkan (AC12)."""
        # Act
        isi = _pdf(_tren(score_scope=scope))

        # Assert
        assert b"Tren berisi skor dev, hanya untuk internal dan tidak diterbitkan." in isi

    def test_tanda_internal_dalam_bahasa_inggris(self):
        """Positif: tanda yang sama dalam bahasa Inggris."""
        # Act
        isi = _pdf(_tren(score_scope=ScoreScope.DEV), language=ReportLanguage.EN)

        # Assert
        assert b"This trend contains dev scores. Internal only, it is never published." in isi

    def test_tren_test_tidak_diberi_tanda_skor_dev(self):
        """Negatif: skor resmi (test) tidak diberi tanda internal."""
        # Act
        isi = _pdf(_tren(score_scope=ScoreScope.TEST))

        # Assert
        assert b"skor dev" not in isi


# --- Grafik garis ---------------------------------------------------------------
# Grafik diperiksa dari objek Drawing-nya, karena isi grafik di PDF hanya garis dan kotak.


def _plot(tren: TrendRead, bahasa=ReportLanguage.ID) -> LinePlot:
    gambar = _grafik_garis(tren, bahasa)
    return next(isi for isi in gambar.contents if isinstance(isi, LinePlot))


def _panduan_putus(tren: TrendRead) -> list[Line]:
    gambar = _grafik_garis(tren, ReportLanguage.ID)
    return [isi for isi in gambar.contents if isinstance(isi, Line)]


class TestLineChart:
    def test_titik_yang_tersambung_menjadi_satu_garis(self):
        """Positif: satu garis untuk satu metrik (AC3)."""
        # Act
        plot = _plot(_tren())

        # Assert
        assert plot.data == [[(0, 0.83), (1, 0.61)]]

    def test_garis_diputus_di_titik_yang_versinya_berubah(self):
        """Positif: versi berubah, garis diputus pada titik perubahan (AC5)."""
        # Arrange
        tren = _tren(
            points=[
                _titik(9, "0.50"),
                _titik(10, "0.60", breaks=[RUBRIC_VERSION]),
                _titik(11, "0.70"),
            ]
        )

        # Act
        plot = _plot(tren)

        # Assert
        assert plot.data == [[(0, 0.5)], [(1, 0.6), (2, 0.7)]]

    def test_titik_putus_diberi_garis_bantu_putus_putus(self):
        """Positif: titik perubahan ditandai di grafik, alasannya ada di tabel (AC5)."""
        # Arrange
        tren = _tren(points=[_titik(9), _titik(10, breaks=[RUBRIC_VERSION]), _titik(11)])

        # Act
        panduan = _panduan_putus(tren)

        # Assert
        assert len(panduan) == 1
        assert panduan[0].strokeDashArray
        assert panduan[0].x1 == panduan[0].x2

    def test_tanpa_putus_tidak_ada_garis_bantu_dan_tidak_ada_garis_target(self):
        """Negatif: tidak ada garis lain selain garis tren, termasuk garis target (AC7)."""
        # Act
        panduan = _panduan_putus(_tren())
        plot = _plot(_tren())

        # Assert
        assert panduan == []
        assert len(plot.data) == 1

    def test_nilai_kosong_tidak_digambar_sebagai_nol_dan_memutus_garis(self):
        """Negatif: titik tanpa nilai bukan skor 0, dan garis tidak melompatinya."""
        # Arrange
        tren = _tren(points=[_titik(9, "0.50"), _titik(10, None), _titik(11, "0.70")])

        # Act
        plot = _plot(tren)

        # Assert
        assert plot.data == [[(0, 0.5)], [(2, 0.7)]]

    def test_bulan_tanpa_sesi_menjadi_celah_di_sumbu_waktu(self):
        """Edge: bulan tanpa sesi dikosongkan (D4), jarak antar bulan tetap sesuai waktu."""
        # Arrange
        tren = _tren(points=[_titik(9, "0.50"), _titik(11, "0.70")])

        # Act
        plot = _plot(tren)

        # Assert
        assert plot.data == [[(0, 0.5), (2, 0.7)]]
        assert plot.xValueAxis.valueSteps == [0, 2]

    def test_sumbu_waktu_melewati_pergantian_tahun(self):
        """Edge: Desember ke Januari berjarak satu bulan."""
        # Arrange
        tren = _tren(points=[_titik(12, "0.50", tahun=2025), _titik(1, "0.70", tahun=2026)])

        # Act
        plot = _plot(tren)

        # Assert
        assert plot.data == [[(0, 0.5), (1, 0.7)]]

    def test_satu_titik_tetap_digambar_sebagai_titik(self):
        """Edge: produk baru dengan satu bulan pengukuran (AC6)."""
        # Act
        plot = _plot(_tren(points=[_titik(10, "0.83")]))

        # Assert
        assert plot.data == [[(0, 0.83)]]

    def test_segmen_tanpa_kasus_sama_tidak_digambar_tetapi_segmen_lain_tetap(self):
        """Edge: segmen tanpa kasus sama tidak menghapus segmen lain (AC11)."""
        # Arrange
        tren = _tren(
            points=[
                _titik(8, None, case_count=0),
                _titik(9, None, case_count=0),
                _titik(10, "0.40", breaks=[RUBRIC_VERSION]),
                _titik(11, "0.90"),
            ],
            warnings=[NO_SHARED_CASES],
        )

        # Act
        plot = _plot(tren)

        # Assert
        assert plot.data == [[(2, 0.4), (3, 0.9)]]

    def test_semua_nilai_kosong_tidak_ada_grafik(self):
        """Edge: tidak ada kasus sama sama sekali, peringatan tanpa garis (AC11)."""
        # Arrange
        tren = _tren(
            points=[_titik(10, None, case_count=0), _titik(11, None, case_count=0)],
            warnings=[NO_SHARED_CASES],
        )

        # Act
        gambar = _grafik_garis(tren, ReportLanguage.ID)
        isi = _pdf(tren)

        # Assert
        assert gambar is None
        assert isi.startswith(b"%PDF")

    def test_tren_kosong_tidak_ada_grafik(self):
        """Edge: tanpa titik tidak ada grafik."""
        # Act
        gambar = _grafik_garis(_tren(points=[]), ReportLanguage.ID)

        # Assert
        assert gambar is None

    def test_label_sumbu_mengikuti_bahasa(self):
        """Positif: nama bulan singkat dan pemisah desimal mengikuti bahasa."""
        # Act
        plot_id = _plot(_tren(), ReportLanguage.ID)
        plot_en = _plot(_tren(), ReportLanguage.EN)

        # Assert
        assert plot_id.xValueAxis.labelTextFormat(0) == "Okt 26"
        assert plot_en.xValueAxis.labelTextFormat(0) == "Oct 26"
        assert plot_id.yValueAxis.labelTextFormat(0.5) == "0,50"
        assert plot_en.yValueAxis.labelTextFormat(0.5) == "0.50"
        assert (plot_id.yValueAxis.valueMin, plot_id.yValueAxis.valueMax) == (0, 1)

    def test_lebih_dari_dua_belas_titik_label_bulan_diselang(self):
        """Edge: rentang semua bisa lebih dari 12 titik; label diselang supaya tidak bertumpuk."""
        # Arrange
        titik = [_titik(bulan, tahun=tahun) for tahun in (2025, 2026) for bulan in range(1, 13)]

        # Act
        plot = _plot(_tren(points=titik))

        # Assert
        assert plot.xValueAxis.labelTextFormat(0) == "Jan 25"
        assert plot.xValueAxis.labelTextFormat(1) == ""
        assert plot.xValueAxis.labelTextFormat(2) == "Mar 25"

    def test_grafik_masuk_ke_berkas(self):
        """Positif: grafik ikut di PDF (label sumbu bulan hanya ada di grafik)."""
        # Act
        isi = _pdf(_tren())

        # Assert
        assert b"Okt 26" in isi
        assert b"Nov 26" in isi


# --- Nama berkas -------------------------------------------------------------


class TestFilename:
    def test_nama_berkas_dari_id_produk_pilihan_bahasa_dan_bulan_dibuat(self):
        """Positif: nama berkas dari id dan nilai enum saja, bulan dibuat dalam WIB."""
        # Act
        nama = trend_pdf_filename(_tren(), language=ReportLanguage.ID, generated_at=DIBUAT)

        # Assert
        assert nama == f"tren-{PRODUK}-test-total-all-id-2026-11.pdf"

    def test_bulan_di_nama_berkas_memakai_wib(self):
        """Edge: 30 Nov 18.00 UTC sudah Desember di WIB."""
        # Act
        nama = trend_pdf_filename(
            _tren(metric=TrendMetric.LLM, score_scope=ScoreScope.DEV, range=TrendRange.SIX_MONTHS),
            language=ReportLanguage.EN,
            generated_at=datetime(2026, 11, 30, 18, 0, tzinfo=UTC),
        )

        # Assert
        assert nama == f"tren-{PRODUK}-dev-llm-6m-en-2026-12.pdf"

    def test_nama_produk_tidak_pernah_masuk_nama_berkas(self):
        """Negatif: nama produk diketik pengguna, jadi tidak dipakai di Content-Disposition."""
        # Act
        nama = trend_pdf_filename(
            _tren(product_name='a"; rm -rf\r\nX-Header: 1'),
            language=ReportLanguage.ID,
            generated_at=DIBUAT,
        )

        # Assert
        assert re.fullmatch(r"[a-z0-9\-]+\.pdf", nama)
