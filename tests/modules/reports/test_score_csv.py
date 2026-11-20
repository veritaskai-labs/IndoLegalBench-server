"""Isi berkas CSV skor satu sesi. PBI-16 SCRUM-83 SCRUM-327.

AC5 (isi dan catatan versi) dan usulan teknis netralisasi sel formula. Murni, tanpa DB.
Review PR #152: kasus yang tidak dinilai tetap tertulis dengan status, versi yang tidak
diketahui tertulis "tidak tercatat", dan formula di balik spasi atau baris baru dinetralkan.
"""

import csv
import io
from decimal import Decimal

import pytest

from app.modules.reports.models import MeasurementKind
from app.modules.reports.score_csv import (
    HEADER,
    TIDAK_TERCATAT,
    ScoreCsvRow,
    ScoreCsvSession,
    neutralize_cell,
    render_score_csv,
)


def _sesi(**ubah) -> ScoreCsvSession:
    bawaan = {
        "product_name": "DeepSeek",
        "measurement_kind": MeasurementKind.AUTOMATIC,
        "rubric_version": "3",
        "product_model_version": "deepseek-v3",
        "judge_model_version": "judge-1",
        "trial": False,
    }
    return ScoreCsvSession(**{**bawaan, **ubah})


def _baris(**ubah) -> ScoreCsvRow:
    bawaan = {
        "case_code": "PDT-001",
        "case_version": "2",
        "det_score": Decimal("0.8"),
        "llm_score": Decimal("0.75"),
        "graded": True,
    }
    return ScoreCsvRow(**{**bawaan, **ubah})


def _baca(isi: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(isi.decode("utf-8-sig"))))


# AC5: susunan kolom.


def test_kolom_berisi_kasus_produk_skor_jenis_dan_catatan_versi():
    assert HEADER == [
        "Kasus",
        "Versi Kasus",
        "Produk",
        "Jenis Pengukuran",
        "Status Penilaian",
        "Skor Deterministik",
        "Skor LLM",
        "Versi Rubrik",
        "Versi Model Produk",
        "Model Penilai",
        "Uji Coba",
    ]


def test_tanpa_kolom_jawaban_asli_atau_alasan_penilaian():
    terlarang = {"jawaban", "alasan", "answer", "reason", "rationale"}

    assert not {kolom.lower() for kolom in HEADER} & terlarang


def test_satu_baris_per_kasus_dengan_catatan_sesi_di_setiap_baris():
    isi = render_score_csv(_sesi(), (_baris(), _baris(case_code="PDT-002", case_version="1")))

    baris = _baca(isi)
    assert [b["Kasus"] for b in baris] == ["PDT-001", "PDT-002"]
    assert [b["Versi Kasus"] for b in baris] == ["2", "1"]
    assert {b["Produk"] for b in baris} == {"DeepSeek"}
    assert {b["Jenis Pengukuran"] for b in baris} == {"automatic"}
    assert {b["Versi Rubrik"] for b in baris} == {"3"}
    assert {b["Versi Model Produk"] for b in baris} == {"deepseek-v3"}
    assert {b["Model Penilai"] for b in baris} == {"judge-1"}


def test_jenis_pengukuran_manual_tertulis_manual():
    isi = render_score_csv(_sesi(measurement_kind=MeasurementKind.MANUAL), (_baris(),))

    assert _baca(isi)[0]["Jenis Pengukuran"] == "manual"


def test_sesi_tanpa_kasus_dinilai_tetap_berkas_berisi_header():
    isi = render_score_csv(_sesi(), ())

    assert _baca(isi) == []
    assert isi.decode("utf-8-sig").splitlines()[0].split(",") == HEADER


# AC5: skor dua desimal, kosong bukan nol.


@pytest.mark.parametrize(
    ("skor", "tertulis"),
    [("0.8", "0.80"), ("0.805", "0.81"), ("0.004", "0.00"), ("1", "1.00"), ("0.6666", "0.67")],
)
def test_skor_ditulis_dua_desimal_dibulatkan_setengah_ke_atas(skor, tertulis):
    isi = render_score_csv(_sesi(), (_baris(det_score=Decimal(skor), llm_score=Decimal(skor)),))

    baris = _baca(isi)[0]
    assert baris["Skor Deterministik"] == tertulis
    assert baris["Skor LLM"] == tertulis


def test_skor_yang_belum_ada_ditulis_kosong_bukan_nol():
    isi = render_score_csv(_sesi(), (_baris(det_score=None, llm_score=Decimal("0.5")),))

    baris = _baca(isi)[0]
    assert baris["Skor Deterministik"] == ""
    assert baris["Skor LLM"] == "0.50"


# D2: sesi Uji Coba wajib bertanda di CSV.


def test_sesi_uji_coba_bertanda_di_setiap_baris():
    isi = render_score_csv(_sesi(trial=True), (_baris(), _baris(case_code="PDT-002")))

    assert {b["Uji Coba"] for b in _baca(isi)} == {"ya"}


def test_sesi_resmi_bukan_uji_coba():
    isi = render_score_csv(_sesi(trial=False), (_baris(),))

    assert _baca(isi)[0]["Uji Coba"] == "tidak"


def test_versi_yang_tidak_diketahui_ditulis_tidak_tercatat_bukan_kosong():
    isi = render_score_csv(
        _sesi(rubric_version="", product_model_version="", judge_model_version=""),
        (_baris(case_version=""),),
    )

    baris = _baca(isi)[0]
    assert TIDAK_TERCATAT == "tidak tercatat"
    assert baris["Versi Kasus"] == TIDAK_TERCATAT
    assert baris["Versi Rubrik"] == TIDAK_TERCATAT
    assert baris["Versi Model Produk"] == TIDAK_TERCATAT
    assert baris["Model Penilai"] == TIDAK_TERCATAT


# Review PR #152 B1: kasus tanpa skor tetap muncul, dengan status yang jelas.


def test_kasus_yang_dinilai_berstatus_dinilai():
    isi = render_score_csv(_sesi(), (_baris(),))

    assert _baca(isi)[0]["Status Penilaian"] == "dinilai"


def test_kasus_yang_tidak_dinilai_tetap_tertulis_dengan_skor_kosong():
    isi = render_score_csv(_sesi(), (_baris(graded=False, det_score=None, llm_score=None),))

    baris = _baca(isi)[0]
    assert baris["Status Penilaian"] == "tidak dinilai"
    assert baris["Skor Deterministik"] == ""
    assert baris["Skor LLM"] == ""
    assert baris["Versi Rubrik"] == "3"


# Usulan teknis: netralkan sel yang dibaca spreadsheet sebagai formula.


@pytest.mark.parametrize("awal", ["=", "+", "-", "@", "\t", "\r"])
def test_sel_diawali_karakter_formula_diberi_tanda_kutip(awal):
    assert neutralize_cell(f"{awal}SUM(A1)") == f"'{awal}SUM(A1)"


@pytest.mark.parametrize(
    "nilai", ["\n=SUM(A1)", " =SUM(A1)", "   +1+1", "\t =1", "\r\n@SUM(1)", "\n"]
)
def test_formula_di_balik_spasi_atau_baris_baru_juga_dinetralkan(nilai):
    assert neutralize_cell(nilai) == "'" + nilai


def test_sel_biasa_tidak_diubah():
    assert neutralize_cell("DeepSeek") == "DeepSeek"
    assert neutralize_cell("") == ""
    assert neutralize_cell(" PDT 001") == " PDT 001"
    assert neutralize_cell("a=b") == "a=b"


def test_nama_produk_dan_kode_kasus_berformula_dinetralkan_di_berkas():
    isi = render_score_csv(
        _sesi(product_name='=HYPERLINK("http://jahat.test")'),
        (_baris(case_code="@SUM(1+1)"),),
    )

    baris = _baca(isi)[0]
    assert baris["Produk"] == '\'=HYPERLINK("http://jahat.test")'
    assert baris["Kasus"] == "'@SUM(1+1)"


def test_versi_model_berformula_juga_dinetralkan():
    isi = render_score_csv(_sesi(product_model_version="+cmd|' /C calc'!A0"), (_baris(),))

    assert _baca(isi)[0]["Versi Model Produk"].startswith("'+")


# Berkas siap dibuka Excel.


def test_berkas_utf8_dengan_bom_supaya_excel_membaca_huruf_non_ascii():
    isi = render_score_csv(_sesi(product_name="Justisio Ä"), (_baris(),))

    assert isi.startswith("﻿".encode())
    assert _baca(isi)[0]["Produk"] == "Justisio Ä"
