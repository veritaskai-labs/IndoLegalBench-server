"""Test formula kelengkapan kasus (SCRUM-107)."""

from typing import Any

from app.modules.cases.completeness import evaluate


def payload(**overrides: Any) -> dict[str, Any]:
    """Body kasus yang lengkap, bisa ditimpa per field."""
    data: dict[str, Any] = {
        "case_code": "PHK-001",
        "identity": {
            "title": "Pemutusan hubungan kerja sepihak",
            "question": "Apakah PHK tanpa surat peringatan sah?",
            "category": "ketenagakerjaan",
        },
        "legal_refs": [
            {
                "regulation_type": "UU",
                "regulation_number": "13",
                "year": 2003,
                "pasal": "151",
            }
        ],
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
    hasil = evaluate(
        payload(legal_refs=[{"regulation_type": "UU", "regulation_number": "13", "year": 2003}])
    )

    assert hasil["legal_ref_count"] == 0
    assert "legal_refs" in missing_fields(hasil)


def test_rujukan_kosong_dilaporkan():
    hasil = evaluate(payload(legal_refs=[]))

    assert hasil["legal_ref_count"] == 0
    assert "legal_refs" in missing_fields(hasil)


def test_beberapa_rujukan_dihitung_semua():
    hasil = evaluate(
        payload(
            legal_refs=[
                {"regulation_type": "UU", "regulation_number": "13", "pasal": "151"},
                {"regulation_type": "PP", "regulation_number": "35", "pasal": "40"},
            ]
        )
    )

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
