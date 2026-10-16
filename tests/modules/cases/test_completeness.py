"""Test formula kelengkapan kasus (SCRUM-107)."""

from typing import Any

import pytest

from app.modules.cases.completeness import evaluate, review_blockers
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001, rujukan_pasal


def payload(**overrides: Any) -> dict[str, Any]:
    """Body kasus yang lengkap, bisa ditimpa per field."""
    data: dict[str, Any] = {
        "case_code": "PHK-001",
        "identity": {
            "title": "Pemutusan hubungan kerja sepihak",
            "question": "Apakah PHK tanpa surat peringatan sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [rujukan_pasal("151")],
        "answer_criteria": {
            "must_contain": ["surat peringatan"],
            "must_not_contain": [],
            "expected_conclusion": "PHK tidak sah",
        },
        "traps": [
            {
                "description": "Menyebut pasal yang sudah dicabut",
                "expected_model_behavior": "Menolak memakai pasal itu",
            }
        ],
        "split_tag": "dev",
    }
    data.update(overrides)
    return data


def missing_fields(hasil: dict[str, Any]) -> set[str]:
    return {item["field"] for item in hasil["missing"]}


def test_kasus_lengkap_siap_review():
    hasil = evaluate(payload())

    assert hasil["is_complete"] is True
    assert hasil["ready_for_review"] is True
    assert hasil["missing"] == []
    assert hasil["trap_count"] == 1
    assert hasil["legal_ref_count"] == 1
    assert hasil["pct"] == 100


def test_judul_kosong_dilaporkan():
    hasil = evaluate(payload(identity={"title": "   ", "question": "Ada?"}))

    assert hasil["is_complete"] is False
    assert "identity.title" in missing_fields(hasil)


def test_pertanyaan_kosong_dilaporkan():
    hasil = evaluate(payload(identity={"title": "Judul", "question": None}))

    assert "identity.question" in missing_fields(hasil)


def test_kategori_kosong_tidak_menghalangi():
    hasil = evaluate(payload(identity={"title": "Judul", "question": "Ada?"}))

    assert missing_fields(hasil) == set()
    assert hasil["is_complete"] is True


def test_case_code_tidak_sesuai_pola_dilaporkan():
    hasil = evaluate(payload(case_code="-awalan-salah"))

    assert "case_code" in missing_fields(hasil)


def test_rujukan_tanpa_pasal_tidak_dihitung():
    rujukan = rujukan_pasal("151")
    del rujukan["links"][0]["pasal"]

    hasil = evaluate(payload(legal_refs=[rujukan]))

    assert hasil["legal_ref_count"] == 0
    assert "legal_refs" in missing_fields(hasil)


def test_rujukan_kosong_dilaporkan():
    hasil = evaluate(payload(legal_refs=[]))

    assert hasil["legal_ref_count"] == 0
    assert "legal_refs" in missing_fields(hasil)


def test_beberapa_rujukan_dihitung_semua():
    hasil = evaluate(payload(legal_refs=[rujukan_ktk_001(), rujukan_ac10()]))

    assert hasil["legal_ref_count"] == 2


def test_kriteria_jawaban_kosong_dilaporkan():
    hasil = evaluate(
        payload(
            answer_criteria={
                "must_contain": [],
                "must_not_contain": [],
                "expected_conclusion": "",
            }
        )
    )

    assert "answer_criteria" in missing_fields(hasil)


def test_hanya_must_not_contain_sudah_dihitung():
    hasil = evaluate(
        payload(
            answer_criteria={
                "must_contain": [],
                "must_not_contain": ["ganti rugi otomatis"],
                "expected_conclusion": "",
            }
        )
    )

    assert "answer_criteria" not in missing_fields(hasil)


def test_tanpa_jebakan_siap_review():
    hasil = evaluate(payload(traps=[]))

    assert hasil["trap_count"] == 0
    assert hasil["is_complete"] is True
    assert hasil["ready_for_review"] is True
    assert "traps" not in missing_fields(hasil)
    assert hasil["pct"] == 100


def test_jebakan_tanpa_deskripsi_tidak_dihitung():
    hasil = evaluate(payload(traps=[{"expected_model_behavior": "Menolak"}]))

    assert hasil["trap_count"] == 0
    assert "traps" not in missing_fields(hasil)


def test_split_tag_kosong_dilaporkan():
    hasil = evaluate(payload(split_tag=None))

    assert "split_tag" in missing_fields(hasil)


def test_split_tag_tidak_sah_dilaporkan():
    hasil = evaluate(payload(split_tag="produksi"))

    assert "split_tag" in missing_fields(hasil)


def test_draft_kosong_melaporkan_semua_bagian():
    hasil = evaluate(
        {
            "case_code": "",
            "identity": None,
            "legal_refs": None,
            "answer_criteria": None,
            "traps": None,
            "split_tag": None,
        }
    )

    assert hasil["is_complete"] is False
    assert hasil["pct"] == 0
    assert missing_fields(hasil) == {
        "identity.title",
        "identity.question",
        "case_code",
        "split_tag",
        "legal_refs",
        "answer_criteria",
    }
    assert all(item["message"] for item in hasil["missing"])


def test_setengah_terisi_memberi_persentase_di_antaranya():
    hasil = evaluate(payload(traps=[], answer_criteria={}))

    assert 0 < hasil["pct"] < 100


# --- Guard pengajuan review (SCRUM-143) ---------------------------------------


def _ref(pasal: str = "151") -> dict[str, Any]:
    return rujukan_pasal(pasal)


def test_kasus_lengkap_tidak_punya_penghalang():
    # Act + Assert
    assert review_blockers(payload()) == []


def test_penghalang_memuat_bagian_yang_kurang():
    # Act
    hasil = review_blockers(payload(answer_criteria={}, split_tag=None))

    # Assert
    assert [item["field"] for item in hasil] == ["split_tag", "answer_criteria"]


def test_rujukan_tidak_lengkap_tetap_menghalangi_walau_ada_yang_sah():
    """Kelengkapan hanya butuh satu rujukan sah, guard menolak satu pun yang rusak."""
    # Arrange
    data = payload(legal_refs=[_ref(), _ref("  ")])

    # Act
    hasil = review_blockers(data)

    # Assert
    assert evaluate(data)["is_complete"] is True
    assert hasil == [
        {
            "field": "legal_refs[1].links[0].pasal",
            "message": "Rujukan hukum harus lengkap sampai pasal.",
        }
    ]


def test_rujukan_bukan_objek_disebut_per_indeks():
    # Act
    hasil = review_blockers(payload(legal_refs=[_ref(), "UU 13/2003"]))

    # Assert
    assert [item["field"] for item in hasil] == ["legal_refs[1]"]


def test_tanpa_rujukan_sah_tidak_mengulang_pesan_per_field():
    # Act
    hasil = review_blockers(payload(legal_refs=[]))

    # Assert
    assert [item["field"] for item in hasil] == ["legal_refs"]


# --- PBI-21: isi wajib per kind rujukan dan bentuk link (SCRUM-160) -------------


def _penghalang(rujukan: Any) -> list[str]:
    """Field penghalang review untuk satu rujukan di samping rujukan sah."""
    hasil = review_blockers(payload(legal_refs=[rujukan_ktk_001(), rujukan]))
    return [item["field"] for item in hasil]


def _jumlah(rujukan: Any) -> int:
    return evaluate(payload(legal_refs=[rujukan]))["legal_ref_count"]


def test_contoh_ac10_dihitung_lengkap():
    assert _jumlah(rujukan_ac10()) == 1
    assert _penghalang(rujukan_ac10()) == []


def test_link_name_only_tanpa_pasal_dihitung_lengkap():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["form"] = "name_only"
    del rujukan["links"][0]["pasal"]

    assert _jumlah(rujukan) == 1
    assert _penghalang(rujukan) == []


def test_teks_bebas_terisi_dihitung_lengkap():
    """AC7. Isinya input format sintetis."""
    rujukan = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}

    assert _jumlah(rujukan) == 1
    assert _penghalang(rujukan) == []


@pytest.mark.parametrize(
    ("ubah", "field"),
    [
        (lambda r: r["links"][1].pop("angka"), "legal_refs[1].links[1].angka"),
        (lambda r: r["links"][1].update(angka="  "), "legal_refs[1].links[1].angka"),
        (lambda r: r["links"][1].pop("pasal"), "legal_refs[1].links[1].pasal"),
        (lambda r: r["links"][0].pop("pasal"), "legal_refs[1].links[0].pasal"),
        (lambda r: r["links"][0].pop("regulation_type"), "legal_refs[1].links[0].regulation_type"),
        (lambda r: r["links"][0].pop("number"), "legal_refs[1].links[0].number"),
        (lambda r: r["links"][0].update(year=None), "legal_refs[1].links[0].year"),
        (lambda r: r["links"][0].update(form="lain"), "legal_refs[1].links[0].form"),
        (lambda r: r["links"].__setitem__(1, "rusak"), "legal_refs[1].links[1]"),
        (lambda r: r.update(links=[]), "legal_refs[1].links"),
        (lambda r: r.update(links="uu 13"), "legal_refs[1].links"),
        (lambda r: r.pop("kind"), "legal_refs[1].kind"),
    ],
    ids=[
        "pasal_angka-tanpa-angka",
        "pasal_angka-angka-spasi",
        "pasal_angka-tanpa-pasal",
        "pasal-tanpa-pasal",
        "tanpa-jenis",
        "tanpa-nomor",
        "tahun-null-data-lama",
        "bentuk-tidak-dikenal",
        "link-bukan-objek",
        "structured-tanpa-link",
        "links-bukan-daftar",
        "tanpa-kind-bentuk-sprint-1",
    ],
)
def test_rujukan_tidak_lengkap_menunjuk_field_pertama_yang_kurang(ubah, field):
    rujukan = rujukan_ac10()
    ubah(rujukan)

    assert _jumlah(rujukan) == 0
    assert _penghalang(rujukan) == [field]


def test_link_pertama_yang_rusak_dilaporkan_lebih_dulu():
    rujukan = rujukan_ac10()
    del rujukan["links"][0]["pasal"]
    del rujukan["links"][1]["angka"]

    assert _penghalang(rujukan) == ["legal_refs[1].links[0].pasal"]


@pytest.mark.parametrize("isi", [None, "", "   "])
def test_teks_bebas_kosong_menghalangi(isi):
    rujukan = {"kind": "free_text", "links": [], "free_text": isi}

    assert _jumlah(rujukan) == 0
    assert _penghalang(rujukan) == ["legal_refs[1].free_text"]


def test_rujukan_bentuk_sprint_1_tersimpan_tidak_meledak():
    """Konten lama di JSONB belum punya kind/links: dihitung belum lengkap, bukan error."""
    lama = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "151"}

    assert _jumlah(lama) == 0
    assert _penghalang(lama) == ["legal_refs[1].kind"]
