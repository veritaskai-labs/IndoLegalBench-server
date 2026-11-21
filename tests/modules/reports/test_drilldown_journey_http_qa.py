"""QA alur penelusuran angka laporan sampai jawaban asli. PBI-16 (SCRUM-83) AC1, AC2; SCRUM-330.

Klik angka -> daftar kasus -> satu kasus -> percobaan, di atas laporan yang dibuat dari sesi
sungguhan (drilldown_qa). Angka laporan harus cocok dengan skor kasus di baliknya, juga
setelah sesinya dinilai ulang (D1).

Label: Positif = boleh dan berhasil, Edge = batas perilaku.
"""

from decimal import ROUND_HALF_UP, Decimal

from tests.modules.reports.drilldown_qa import (
    DIPEROLEH,
    KASUS,
    KASUS_GAGAL_SEKALI,
    KODE_DEV,
    KODE_TEST,
    PenilaiPelit,
    angka,
    baca_laporan,
    buat_laporan,
    daftar_kasus,
    detail_kasus,
    nilai,
)

KODE_PER_SPLIT = {"test": KODE_TEST, "dev": KODE_DEV, "combined": KODE_TEST + KODE_DEV}


def _rata_rata(skor: list[str]) -> str:
    total = sum(Decimal(s) for s in skor) / len(skor)
    return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _telusuri(dunia, laporan) -> dict:
    """Setiap angka laporan dan hasil penelusurannya: (kode kasus, versi, det, llm)."""
    hasil = {}
    for sesi in laporan["sessions"]:
        for angka_ in sesi["results"]:
            kasus = daftar_kasus(dunia, laporan, angka_)["cases"]
            hasil[(sesi["product_label"], angka_["score_split"])] = (
                (angka_["det_score"], angka_["llm_score"], angka_["cases_basis_count"]),
                (
                    _rata_rata([k["det_score"] for k in kasus]),
                    _rata_rata([k["llm_score"] for k in kasus]),
                    len(kasus),
                ),
                sorted(k["case_code"] for k in kasus),
            )
    return hasil


def test_setiap_angka_laporan_terbuka_menjadi_kasus_yang_menyusunnya(dunia):
    """Positif: AC1, keenam angka (2 produk x test/dev/gabungan) sama dengan rata-rata skor
    kasus di baliknya, dan daftarnya berisi tepat kasus split itu beserta versinya."""
    # Arrange
    laporan = buat_laporan(dunia)

    # Act
    ditelusuri = _telusuri(dunia, laporan)

    # Assert
    assert len(ditelusuri) == 6
    for (produk, split), (tertulis, dihitung, kode) in ditelusuri.items():
        assert tertulis == dihitung, (produk, split)
        assert kode == sorted(KODE_PER_SPLIT[split]), (produk, split)
    versi = daftar_kasus(dunia, laporan, angka(laporan, "AiYU", "combined"))["cases"]
    assert {k["case_version_no"] for k in versi} == {1}


def test_satu_kasus_dibuka_sampai_jawaban_asli_skor_alasan_dan_versi_rubrik(dunia):
    """Positif: AC2, jawaban asli setiap percobaan terbaca bersama skor, alasan, dan rubriknya."""
    # Arrange
    laporan = buat_laporan(dunia)
    hasil = angka(laporan, "AiYU", "test")
    rubrik = daftar_kasus(dunia, laporan, hasil)["rubric_version"]

    # Act
    respons = detail_kasus(dunia, laporan, hasil, "PHK-002")

    # Assert
    assert respons.status_code == 200, respons.text
    kasus = respons.json()
    (dinilai,) = [p for p in kasus["attempts"] if p["graded"]]
    jawaban = next(k.jawaban_otomatis for k in KASUS if k.kode == "PHK-002")
    assert (kasus["question"], kasus["rubric_version"]) == (
        "Berapa lama masa percobaan kerja?",
        rubrik,
    )
    assert (dinilai["answer_text"], dinilai["source"]) == (jawaban, "automatic")
    assert (dinilai["det_score"], dinilai["llm_score"]) == (kasus["det_score"], kasus["llm_score"])
    assert dinilai["reasons"] == ["criterion_unmet", "criterion_unmet"]
    assert [c["is_met"] for c in dinilai["criteria"]] == [False, False]


def test_percobaan_gagal_teknis_ditandai_tidak_dinilai_dan_tidak_menurunkan_skor(dunia):
    """Edge: AC2 dan catatan QA, percobaan gagal teknis tampil 'tidak dinilai' (bukan skor 0)
    dan skor kasusnya datang dari percobaan yang berhasil saja."""
    # Arrange
    laporan = buat_laporan(dunia)

    # Act
    kasus = detail_kasus(dunia, laporan, angka(laporan, "AiYU", "test"), KASUS_GAGAL_SEKALI).json()

    # Assert
    gagal, berhasil = sorted(kasus["attempts"], key=lambda p: p["graded"])
    assert (gagal["status"], gagal["graded"]) == ("technical_failure", False)
    assert (gagal["det_score"], gagal["llm_score"], gagal["answer_text"]) == (None, None, None)
    assert (kasus["det_score"], kasus["llm_score"]) == (
        berhasil["det_score"],
        berhasil["llm_score"],
    )


def test_jawaban_manual_berlabel_manual_dengan_pengisi_dan_tanggal(dunia):
    """Positif: AC2, jawaban manual tampil dengan label manual, pengisi, dan tanggal jawaban
    diperoleh, dan skor serta alasannya terbaca dengan cara yang sama dengan otomatis."""
    # Arrange
    laporan = buat_laporan(dunia)
    produk = dunia.manual.produk.name

    # Act
    kasus = detail_kasus(dunia, laporan, angka(laporan, produk, "test"), "PHK-001").json()

    # Assert
    (percobaan,) = kasus["attempts"]
    assert (kasus["measurement_kind"], percobaan["source"]) == ("manual", "manual")
    assert percobaan["filled_by_name"] == "Kontributor QA"
    assert percobaan["answered_at"].startswith(DIPEROLEH.strftime("%Y-%m-%dT%H:%M"))
    assert percobaan["graded"] is True
    assert [c["is_met"] for c in percobaan["criteria"]] == [True, True]


def test_penelusuran_tetap_memakai_angka_beku_setelah_sesi_dinilai_ulang(dunia):
    """Edge: D1, nilai ulang setelah laporan dibuat tidak mengubah angka maupun penelusurannya."""
    # Arrange
    laporan = buat_laporan(dunia)
    sebelum = _telusuri(dunia, laporan)
    dunia.pakai_penilai(PenilaiPelit)

    # Act
    ulang = nilai(dunia, dunia.otomatis.id)
    sesudah = _telusuri(dunia, baca_laporan(dunia, laporan["id"]))

    # Assert
    assert {k["scores"]["quality"] for k in ulang["cases"]} == {"0.00"}
    assert sesudah == sebelum
