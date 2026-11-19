"""Unit test pembuat PDF laporan perbandingan (PBI-15 SCRUM-320).

Fungsi murni: ReportDetail (hasil beku, D1) masuk, bytes PDF keluar.
Tidak ada database dan HTTP, jadi tidak ada yang perlu di-mock.

Isi wajib PDF menurut SCRUM-320 dan diagram D3 langkah 15: logo Veritask
dan judul, label otomatis/manual, penanda D2, versi kasus, rubrik, dan
model, skor deterministik dan LLM terpisah (dua desimal, tanpa persen),
peringkat, tingkat kegagalan terpisah (tidak berlaku untuk manual), dan
bahasa mengikuti kolom reports.language (disiapkan untuk PBI-26).

Label: Positif = jalur normal, Negatif = data yang harus ditolak atau
disembunyikan, Edge = batas atau data tidak lengkap.
"""

import base64
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.models import ReportMarkerCode
from app.modules.reports.pdf import (
    DEFAULT_LOGO_PATH,
    ReportPdfRenderer,
    format_score,
    report_pdf_filename,
)
from app.modules.reports.schemas import (
    MeasurementFilter,
    MeasurementKind,
    ReportCaseBasisRead,
    ReportDetail,
    ReportLanguage,
    ReportMarker,
    ReportResultRead,
    ReportSessionRead,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)

DIBUAT = datetime(2026, 12, 1, 2, 30, tzinfo=UTC)
# PNG 1x1 transparan, cukup untuk membuktikan logo berkas dipakai bila ada.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _hasil(split=ScoreSplit.TEST, **kolom) -> ReportResultRead:
    nilai = {
        "score_split": split,
        "cases_graded_count": 120,
        "cases_basis_count": 100,
        "det_score": Decimal("0.81"),
        "llm_score": Decimal("0.74"),
        "total_score": Decimal("0.78"),
        "failure_rate": Decimal("2.50"),
        "rank": 1,
        "repetitions": 3,
        "product_model_version": "gemini-2.5-pro",
        "judge_model_version": "claude-opus-5-5",
    }
    nilai.update(kolom)
    return ReportResultRead(**nilai)


def _sesi(
    label, kind=MeasurementKind.AUTOMATIC, markers=(), results=None, is_trial=False
) -> ReportSessionRead:
    return ReportSessionRead(
        session_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product_label=label,
        measurement_kind=kind,
        is_trial=is_trial,
        markers=list(markers),
        results=results if results is not None else [_hasil()],
    )


def _laporan(**kolom) -> ReportDetail:
    """Laporan pertama versi kecil: dua produk otomatis dan satu manual (AC acuan laporan pertama)."""
    nilai = {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
        "title": "Perbandingan AI Hukum Desember 2026",
        "score_scope": ScoreScope.TEST,
        "has_dev_scores": False,
        "status": ReportStatus.SAVED,
        "language": ReportLanguage.ID,
        "measurement_filter": MeasurementFilter.ALL,
        "rubric_version_no": 4,
        "judge_model_name": "claude-opus-5-5",
        "case_basis_count": 100,
        "created_by": uuid.uuid4(),
        "created_at": DIBUAT,
        "sessions": [
            _sesi("Gemini", results=[_hasil(rank=1)]),
            _sesi("DeepSeek", results=[_hasil(rank=2, det_score=Decimal("0.70"))]),
            _sesi(
                "Justisio",
                kind=MeasurementKind.MANUAL,
                markers=[ReportMarker(code="repetitions_differ")],
                results=[_hasil(rank=3, failure_rate=None, repetitions=1)],
            ),
        ],
        "case_basis": [
            ReportCaseBasisRead(case_code="UAT-PHK-001", case_version_no=2, split_tag=SplitTag.TEST)
        ],
    }
    nilai.update(kolom)
    return ReportDetail(**nilai)


def _pdf(laporan: ReportDetail, **opsi) -> bytes:
    return ReportPdfRenderer(compress=False, **opsi).render(laporan)


# --- Isi laporan -------------------------------------------------------------


def test_pdf_memuat_judul_produk_dan_versi_dasar_perhitungan():
    """Positif: angka di PDF bisa dijelaskan, versi kasus, rubrik, dan model ikut tercetak (AC versi)."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert isi.startswith(b"%PDF")
    assert b"Perbandingan AI Hukum Desember 2026" in isi
    for produk in (b"Gemini", b"DeepSeek", b"Justisio"):
        assert produk in isi
    assert b"UAT-PHK-001 v2" in isi
    assert b"Rubrik v4" in isi
    assert b"claude-opus-5-5" in isi
    assert b"gemini-2.5-pro" in isi


def test_pdf_memberi_label_otomatis_dan_manual():
    """Positif: setiap produk diberi label jenis pengukuran, keduanya tampil di laporan yang sama."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert b"Otomatis" in isi
    assert b"Manual" in isi


def test_pdf_tingkat_kegagalan_tidak_berlaku_untuk_produk_manual():
    """Positif: kegagalan teknis tampil terpisah, dan untuk produk manual tertulis tidak berlaku."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert b"2,50%" in isi
    assert b"Tidak berlaku" in isi


def test_pdf_produk_otomatis_tanpa_angka_kegagalan_tidak_dicetak_nol():
    """Edge: failure_rate otomatis yang kosong tidak boleh terbaca sebagai 0% kegagalan."""
    # Arrange
    laporan = _laporan(sessions=[_sesi("AiYU", results=[_hasil(failure_rate=None)])])

    # Act
    isi = _pdf(laporan)

    # Assert
    assert b"0,00%" not in isi
    assert b"Tidak berlaku" not in isi


def test_pdf_mencetak_penanda_d2_per_produk():
    """Positif: penanda 'jumlah percobaan berbeda' dari D2 ikut tercetak di baris produknya."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert b"Jumlah percobaan berbeda" in isi


@pytest.mark.parametrize(("is_trial", "tercetak"), [(True, True), (False, False)])
def test_pdf_tanda_uji_coba_mengikuti_kolom_is_trial(is_trial, tercetak):
    """Positif dan negatif: sesi Uji Coba wajib diberi tanda (AC sesi layak), sesi resmi tidak.

    Sumbernya kolom is_trial, bukan markers (keputusan SCRUM-300), jadi tanda ini tidak
    bisa hilang walau markers kosong.
    """
    # Arrange
    laporan = _laporan(sessions=[_sesi("AiYU", is_trial=is_trial)])

    # Act
    isi = _pdf(laporan)

    # Assert
    assert (b"Uji Coba" in isi) is tercetak


@pytest.mark.parametrize("bahasa", list(ReportLanguage))
@pytest.mark.parametrize("kode", list(ReportMarkerCode))
def test_setiap_kode_penanda_punya_label_di_kedua_bahasa(kode, bahasa):
    """Edge: kode baru di ReportMarkerCode (SCRUM-300) tidak boleh tercetak mentah di PDF."""
    # Arrange
    laporan = _laporan(language=bahasa, sessions=[_sesi("AiYU", markers=[ReportMarker(code=kode)])])

    # Act
    isi = _pdf(laporan)

    # Assert
    assert kode.value.encode() not in isi


def test_pdf_penanda_tak_dikenal_tetap_dicetak_dengan_kodenya():
    """Edge: penanda baru dari SCRUM-301 yang belum punya label tidak boleh hilang diam-diam."""
    # Arrange
    laporan = _laporan(sessions=[_sesi("AiYU", markers=[ReportMarker(code="new_marker")])])

    # Act
    isi = _pdf(laporan)

    # Assert
    assert b"new_marker" in isi


def test_pdf_memuat_teks_permintaan_isi_kasus_lewat_email():
    """Positif: pembaca diarahkan ke email Veritask untuk isi kasus (PBI-16, teks pada laporan)."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert b"indolegalbench@veritask.ai" in isi


def test_pdf_laporan_dev_diberi_tanda_dan_bagian_per_jenis_nilai():
    """Positif: laporan Dev + Test punya tiga bagian (test, dev, gabungan) dan tanda laporan dev (D3)."""
    # Arrange
    laporan = _laporan(
        score_scope=ScoreScope.DEV_TEST,
        has_dev_scores=True,
        sessions=[
            _sesi("AiYU", results=[_hasil(split) for split in ScoreSplit]),
        ],
    )

    # Act
    isi = _pdf(laporan)

    # Assert
    assert b"Laporan berisi skor dev" in isi
    for bagian in (b"Kasus test", b"Kasus dev", b"Gabungan dev + test"):
        assert bagian in isi


def test_pdf_laporan_test_tidak_mencetak_bagian_dev():
    """Negatif: laporan Test tidak boleh memuat kata kunci bagian dev atau tanda laporan dev."""
    # Act
    isi = _pdf(_laporan())

    # Assert
    assert b"Kasus dev" not in isi
    assert b"Laporan berisi skor dev" not in isi


def test_pdf_bahasa_inggris_memakai_label_inggris():
    """Positif: PDF dibuat dalam bahasa laporan (reports.language, disiapkan untuk PBI-26)."""
    # Act
    isi = _pdf(_laporan(language=ReportLanguage.EN))

    # Assert
    assert b"Automatic" in isi
    assert b"Not applicable" in isi
    assert b"2.50%" in isi
    assert b"Otomatis" not in isi


def test_pdf_tanpa_hasil_tetap_jadi_file_dengan_keterangan():
    """Edge: laporan tanpa baris hasil tetap menghasilkan PDF yang menjelaskan kosongnya."""
    # Act
    isi = _pdf(_laporan(sessions=[], case_basis=[]))

    # Assert
    assert isi.startswith(b"%PDF")
    assert b"Tidak ada hasil" in isi


def test_pdf_teks_mirip_markup_tidak_merusak_file():
    """Edge: judul dan label diketik pengguna, Paragraph reportlab membaca tag XML."""
    # Act
    isi = _pdf(_laporan(title="<b>Uji</b> & <x>", sessions=[_sesi("A&B <C>")]))

    # Assert
    assert isi.startswith(b"%PDF")


def test_pdf_memakai_berkas_logo_bila_tersedia(tmp_path):
    """Positif: logo Veritask dari berkas dipakai di bagian atas laporan (AC tampilan)."""
    # Arrange
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG_1X1)

    # Act
    isi = _pdf(_laporan(), logo_path=logo)

    # Assert
    assert b"/Subtype /Image" in isi


def test_logo_resmi_ikut_di_repo_dan_dipakai_bawaan():
    """Positif: logo Veritask ada di app/ (ikut ter-copy ke image Docker) dan dipakai tanpa opsi."""
    # Act
    isi = ReportPdfRenderer(compress=False).render(_laporan())

    # Assert
    assert DEFAULT_LOGO_PATH.is_file()
    assert b"/Subtype /Image" in isi


def test_pdf_tanpa_berkas_logo_memakai_wordmark(tmp_path):
    """Edge: berkas logo belum ada, PDF tetap jadi dengan tulisan Veritask di bagian atas."""
    # Act
    isi = _pdf(_laporan(), logo_path=tmp_path / "tidak-ada.png")

    # Assert
    assert b"/Subtype /Image" not in isi
    assert b"VERITASK" in isi


def test_pdf_produk_diurutkan_sesuai_peringkat_tanpa_peringkat_di_akhir():
    """Positif dan edge: urutan tabel mengikuti rank; produk dengan skor kosong (rank NULL, D1) di akhir."""
    # Arrange
    laporan = _laporan(
        sessions=[
            _sesi("Zeta", results=[_hasil(rank=None, total_score=None, llm_score=None)]),
            _sesi("Alpha", results=[_hasil(rank=2)]),
            _sesi("Beta", results=[_hasil(rank=1)]),
        ]
    )

    # Act
    isi = _pdf(laporan)

    # Assert
    assert isi.index(b"Beta") < isi.index(b"Alpha") < isi.index(b"Zeta")


def test_pdf_banyak_produk_berlanjut_ke_halaman_berikut():
    """Edge: tidak ada batas jumlah produk (AC1), tabel panjang pindah halaman tanpa terpotong."""
    # Arrange
    laporan = _laporan(sessions=[_sesi(f"Produk {n}", results=[_hasil(rank=n)]) for n in range(60)])

    # Act
    isi = _pdf(laporan)

    # Assert
    assert len(re.findall(rb"/Type /Page\b", isi)) > 1


# --- Format angka dan nama berkas --------------------------------------------


@pytest.mark.parametrize(
    ("nilai", "bahasa", "teks"),
    [
        # Positif: dua desimal, pemisah desimal mengikuti bahasa.
        (Decimal("0.8"), ReportLanguage.EN, "0.80"),
        (Decimal("0.8"), ReportLanguage.ID, "0,80"),
        # Edge: pembulatan setengah ke atas seperti score_aggregation.round_score.
        (Decimal("0.125"), ReportLanguage.EN, "0.13"),
        # Edge: skor kosong (total_score NULL menurut D1) tidak dicetak sebagai 0.
        (None, ReportLanguage.ID, "–"),
    ],
)
def test_format_score_dua_desimal_tanpa_persen(nilai, bahasa, teks):
    """Positif dan edge: skor tampil dua desimal tanpa persen dan tanpa format 'X dari N' (AC skor)."""
    # Act
    hasil = format_score(nilai, bahasa)

    # Assert
    assert hasil == teks


def test_nama_berkas_memakai_id_laporan_bukan_judul():
    """Negatif: judul diketik pengguna, jadi tidak dipakai di header Content-Disposition."""
    # Act
    nama = report_pdf_filename(_laporan(title='a"; rm -rf'))

    # Assert
    assert nama == "laporan-00000000-0000-0000-0000-0000000000a1.pdf"
