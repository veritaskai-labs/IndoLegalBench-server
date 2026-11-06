"""SCRUM-222 PR B: bentuk body dan aturan isi versi rubrik baru. PBI-14 AC3, AC7.

Batas per field ada di schema (RubricVersionCreate), jadi ikut ke kontrak OpenAPI dan
validasi client. Aturan yang melibatkan beberapa field ada di rubric_rules, fungsi murni
tanpa database: hasilnya RubricProblem (code, field, message) yang service ubah jadi 422.
Trigger PostgreSQL dari PR A tetap menjadi pengaman terakhir untuk aturan yang sama.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: isi yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import pytest
from pydantic import ValidationError

from app.modules.grading import rubric_rules
from app.modules.grading.schemas import RubricVersionCreate
from tests.modules.grading.rubric_payload import DIMENSI, LULUS_GAGAL, body_rubrik


def _versi(**ubah) -> RubricVersionCreate:
    return RubricVersionCreate.model_validate(body_rubrik(**ubah))


def _aspek(*bobot) -> list[dict]:
    return [
        {"name": f"Dimensi {i}", "grader_path": "llm", "weight": w, "definition": "Definisi."}
        for i, w in enumerate(bobot, start=1)
    ]


def _galat_schema(**ubah) -> set[tuple]:
    with pytest.raises(ValidationError) as info:
        _versi(**ubah)
    return {tuple(galat["loc"]) for galat in info.value.errors()}


# --- Schema: positif ----------------------------------------------------------


def test_rubrik_holistik_tanpa_bobot_diterima_apa_adanya():
    """Positif: bentuk rubrik v1 klien (tiga dimensi, tanpa bobot, level 0–5)."""
    versi = _versi()

    assert [a.name for a in versi.aspects] == list(DIMENSI)
    assert [a.weight for a in versi.aspects] == [None, None, None]
    assert [lv.score for lv in versi.levels] == [5, 4, 3, 2, 1, 0]
    assert rubric_rules.rubric_problem(versi) is None


def test_teks_dirapikan_dari_spasi_di_tepi():
    """Edge: spasi di tepi nama dan label dibuang sebelum aturan diperiksa."""
    aspek = _aspek(None)
    aspek[0]["name"] = "  Dimensi 1  "
    versi = _versi(aspects=aspek, levels=[{"score": 5, "label": " Lulus ", "is_passing": True}])

    assert (versi.aspects[0].name, versi.levels[0].label) == ("Dimensi 1", "Lulus")


def test_penyetuju_teks_bebas_dan_tanggal_diterima():
    """Positif: K10, penyetuju dari tim hukum klien bukan akun aplikasi."""
    versi = _versi(approved_by_name="Penyetuju Sintetis", approved_at="2026-10-27")

    assert rubric_rules.rubric_problem(versi) is None


@pytest.mark.parametrize(
    "bobot",
    [
        ("100",),
        ("33.33", "33.33", "33.34"),
        ("0.01", "99.99"),
    ],
    ids=["satu-aspek-100", "tiga-aspek-pecahan", "batas-terkecil"],
)
def test_bobot_terisi_semua_dengan_total_100_diterima(bobot):
    """Positif dan edge: bobot boleh diisi asal semua terisi dan totalnya tepat 100."""
    versi = _versi(aspects=_aspek(*bobot), levels=LULUS_GAGAL)

    assert rubric_rules.rubric_problem(versi) is None


def test_level_tanpa_deskriptor_diterima():
    """Edge: deskriptor opsional, isi rubrik bisa menyusul (TBA klien)."""
    versi = _versi(levels=LULUS_GAGAL)

    assert versi.levels[0].descriptors == []
    assert rubric_rules.rubric_problem(versi) is None


# --- Schema: negatif ----------------------------------------------------------


@pytest.mark.parametrize(
    ("ubah", "lokasi"),
    [
        ({"aspects": []}, ("aspects",)),
        ({"aspects": _aspek(*([None] * 21))}, ("aspects",)),
        ({"levels": []}, ("levels",)),
        (
            {"levels": [{"score": s, "label": "L", "is_passing": False} for s in range(7)]},
            ("levels",),
        ),
        ({"judge_prompt_quality": "   "}, ("judge_prompt_quality",)),
        ({"judge_prompt_format": ""}, ("judge_prompt_format",)),
        ({"approved_by_name": " "}, ("approved_by_name",)),
        ({"effective_date": "bukan-tanggal"}, ("effective_date",)),
    ],
    ids=[
        "tanpa-aspek",
        "aspek-21",
        "tanpa-level",
        "level-7",
        "prompt-kualitas-spasi",
        "prompt-format-kosong",
        "penyetuju-spasi",
        "tanggal-salah",
    ],
)
def test_isian_versi_tidak_sah_ditolak_schema(ubah, lokasi):
    assert lokasi in _galat_schema(**ubah)


@pytest.mark.parametrize(
    ("isian", "kolom"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"definition": ""}, "definition"),
        ({"weight": "0"}, "weight"),
        ({"weight": "-1"}, "weight"),
        ({"weight": "100.01"}, "weight"),
        ({"weight": "33.333"}, "weight"),
        ({"grader_path": "manual"}, "grader_path"),
    ],
    ids=[
        "nama-spasi",
        "nama-201",
        "definisi-kosong",
        "bobot-0",
        "bobot-negatif",
        "bobot-di-atas-100",
        "bobot-3-desimal",
        "jalur-tak-dikenal",
    ],
)
def test_isian_aspek_tidak_sah_ditolak_schema(isian, kolom):
    aspek = _aspek(None)
    aspek[0].update(isian)

    assert ("aspects", 0, kolom) in _galat_schema(aspects=aspek)


@pytest.mark.parametrize(
    ("isian", "kolom"),
    [({"score": 6}, "score"), ({"score": -1}, "score"), ({"label": " "}, "label")],
    ids=["skor-6", "skor-negatif", "label-spasi"],
)
def test_isian_level_tidak_sah_ditolak_schema(isian, kolom):
    level = {"score": 5, "label": "L", "is_passing": True} | isian

    assert ("levels", 0, kolom) in _galat_schema(levels=[level])


def test_deskriptor_kosong_ditolak_schema():
    level = {
        "score": 5,
        "label": "L",
        "is_passing": True,
        "descriptors": [{"aspect_name": "Dimensi A", "descriptor": "  "}],
    }

    assert ("levels", 0, "descriptors", 0, "descriptor") in _galat_schema(levels=[level])


def test_field_tak_dikenal_ditolak_schema():
    """Negatif: version_no dan is_active ditentukan server, bukan body (AC7)."""
    lokasi = _galat_schema(version_no=9, is_active=False)

    assert {("version_no",), ("is_active",)} <= lokasi


# --- Aturan antar-field (rubric_rules) -----------------------------------------


def _masalah(**ubah) -> rubric_rules.RubricProblem | None:
    return rubric_rules.rubric_problem(_versi(**ubah))


@pytest.mark.parametrize(
    ("bobot", "kode"),
    [
        (("50", None), rubric_rules.WEIGHTS_INCOMPLETE),
        ((None, "100"), rubric_rules.WEIGHTS_INCOMPLETE),
        (("30", "30", "30"), rubric_rules.WEIGHTS_NOT_100),
        (("50", "50", "0.01"), rubric_rules.WEIGHTS_NOT_100),
    ],
    ids=["campur-kosong-di-akhir", "campur-kosong-di-awal", "total-90", "total-100.01"],
)
def test_bobot_campur_kosong_atau_tidak_total_100_ditolak(bobot, kode):
    """Negatif: K1, bobot semua kosong atau semua terisi dengan total 100."""
    masalah = _masalah(aspects=_aspek(*bobot), levels=LULUS_GAGAL)

    assert (masalah.code, masalah.field) == (kode, "aspects")


@pytest.mark.parametrize("nama_kedua", ["Dimensi 1", "dimensi 1"], ids=["sama", "beda-huruf"])
def test_nama_aspek_ganda_ditolak_tanpa_membedakan_huruf(nama_kedua):
    aspek = _aspek(None, None)
    aspek[1]["name"] = nama_kedua

    masalah = _masalah(aspects=aspek, levels=LULUS_GAGAL)

    assert (masalah.code, masalah.field) == (rubric_rules.DUPLICATE_ASPECT_NAME, "aspects.1.name")


def test_skor_level_ganda_ditolak():
    level = [{"score": 4, "label": f"L{i}", "is_passing": True} for i in range(2)]

    masalah = _masalah(levels=level)

    assert (masalah.code, masalah.field) == (rubric_rules.DUPLICATE_LEVEL_SCORE, "levels.1.score")


def test_deskriptor_untuk_aspek_yang_tidak_ada_ditolak():
    level = {
        "score": 5,
        "label": "L",
        "is_passing": True,
        "descriptors": [{"aspect_name": "Dimensi Z", "descriptor": "Teks."}],
    }

    masalah = _masalah(levels=[level])

    assert (masalah.code, masalah.field) == (
        rubric_rules.UNKNOWN_DESCRIPTOR_ASPECT,
        "levels.0.descriptors.0.aspect_name",
    )


def test_dua_deskriptor_untuk_aspek_yang_sama_di_satu_level_ditolak():
    level = {
        "score": 5,
        "label": "L",
        "is_passing": True,
        "descriptors": [
            {"aspect_name": "Dimensi A", "descriptor": "Satu."},
            {"aspect_name": "dimensi a", "descriptor": "Dua."},
        ],
    }

    masalah = _masalah(levels=[level])

    assert (masalah.code, masalah.field) == (
        rubric_rules.DUPLICATE_DESCRIPTOR,
        "levels.0.descriptors.1.aspect_name",
    )


def test_tanggal_setuju_tanpa_nama_penyetuju_ditolak():
    masalah = _masalah(approved_at="2026-10-27")

    assert (masalah.code, masalah.field) == (rubric_rules.APPROVED_AT_WITHOUT_NAME, "approved_at")


def test_masalah_pertama_yang_dilaporkan_mengikuti_urutan_isian():
    """Edge: beberapa masalah sekaligus, yang dilaporkan yang paling atas di form."""
    aspek = _aspek("50", None)
    aspek[1]["name"] = "Dimensi 1"

    masalah = _masalah(
        aspects=aspek,
        levels=[{"score": 5, "label": "L", "is_passing": True}] * 2,
        approved_at="2026-10-27",
    )

    assert masalah.code == rubric_rules.APPROVED_AT_WITHOUT_NAME


def test_setiap_masalah_punya_pesan_bahasa_indonesia():
    """Edge: pesan dipakai client apa adanya, jadi tidak boleh kosong."""
    masalah = _masalah(approved_at="2026-10-27")

    assert masalah.message.strip()


# --- Level lulus dan tidak lulus (review Kak Aileen #112, blocking 2) ---------------
# Versi baru langsung aktif dan terkunci, jadi rubrik yang tidak bisa meluluskan atau
# tidak bisa menggagalkan jawaban akan menilai semua jawaban sampai versi baru dibuat.


def _level(*pasangan) -> list[dict]:
    return [{"score": s, "label": f"L{s}", "is_passing": lulus} for s, lulus in pasangan]


@pytest.mark.parametrize(
    "level",
    [
        _level((5, True), (4, True), (0, False)),
        _level((5, True), (0, False)),
        _level((1, True), (0, False)),
        _level((0, False), (5, True)),
    ],
    ids=["lulus-4-5", "lulus-5-saja", "batas-terendah", "urutan-body-bebas"],
)
def test_level_lulus_di_atas_semua_level_gagal_diterima(level):
    """Positif dan edge: skor lulus selalu di atas skor gagal; urutan di body bebas."""
    assert _masalah(levels=level) is None


@pytest.mark.parametrize(
    ("level", "kode"),
    [
        (_level((3, False), (0, False)), rubric_rules.LEVELS_WITHOUT_PASSING),
        (_level((5, True), (4, True)), rubric_rules.LEVELS_WITHOUT_FAILING),
        (
            _level(
                (5, True),
            ),
            rubric_rules.LEVELS_WITHOUT_FAILING,
        ),
    ],
    ids=["tanpa-lulus", "tanpa-gagal", "satu-level"],
)
def test_rubrik_tanpa_level_lulus_atau_tanpa_level_gagal_ditolak(level, kode):
    masalah = _masalah(levels=level)

    assert (masalah.code, masalah.field) == (kode, "levels")


@pytest.mark.parametrize(
    ("level", "posisi"),
    [
        (_level((5, False), (4, True), (0, False)), 1),
        (_level((5, True), (3, True), (4, False), (0, False)), 1),
    ],
    ids=["gagal-di-atas-lulus", "lulus-di-bawah-gagal"],
)
def test_level_lulus_di_bawah_level_gagal_ditolak(level, posisi):
    """Negatif: lulus tidak boleh punya skor di bawah level yang gagal."""
    masalah = _masalah(levels=level)

    assert (masalah.code, masalah.field) == (
        rubric_rules.PASSING_BELOW_FAILING,
        f"levels.{posisi}.is_passing",
    )


def test_skor_ganda_dilaporkan_sebelum_aturan_lulus():
    """Edge: masalah skor ganda lebih spesifik, jadi dilaporkan lebih dulu."""
    masalah = _masalah(levels=_level((4, True), (4, True)))

    assert masalah.code == rubric_rules.DUPLICATE_LEVEL_SCORE
