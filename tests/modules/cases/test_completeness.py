from dataclasses import dataclass, field
from typing import Any

from app.modules.cases.completeness import evaluate


@dataclass
class FakeCase:
    """Case minimal untuk test, tidak menyentuh database."""

    title: str | None = "Pemutusan hubungan kerja sepihak"
    question: str | None = "Apakah PHK tanpa surat peringatan sah?"
    category: str | None = "ketenagakerjaan"
    legal_refs: list[dict[str, Any]] | None = field(
        default_factory=lambda: [
            {
                "regulation_type": "UU",
                "regulation_number": "13",
                "year": 2003,
                "pasal": "151",
            }
        ]
    )
    answer_criteria: dict[str, Any] | None = field(
        default_factory=lambda: {
            "must_contain": ["surat peringatan"],
            "must_not_contain": [],
            "expected_conclusion": "PHK tidak sah",
        }
    )
    traps: list[dict[str, Any]] | None = field(
        default_factory=lambda: [
            {
                "description": "Menyebut pasal yang sudah dicabut",
                "expected_model_behavior": "Menolak memakai pasal itu",
            }
        ]
    )
    split_tag: str | None = "dev"


def test_case_lengkap_tidak_punya_field_kurang():
    result = evaluate(FakeCase())

    assert result["is_complete"] is True
    assert result["ready_for_review"] is True
    assert result["missing"] == []
    assert result["trap_count"] == 1
    assert result["legal_ref_count"] == 1


def test_judul_kosong_dilaporkan():
    result = evaluate(FakeCase(title="   "))

    assert result["is_complete"] is False
    assert {m["field"] for m in result["missing"]} == {"identity.title"}


def test_pertanyaan_kosong_dilaporkan():
    result = evaluate(FakeCase(question=None))

    assert {m["field"] for m in result["missing"]} == {"identity.question"}


def test_kategori_kosong_dilaporkan():
    result = evaluate(FakeCase(category=None))

    assert {m["field"] for m in result["missing"]} == {"identity.category"}


def test_rujukan_tanpa_pasal_tidak_dihitung():
    result = evaluate(
        FakeCase(
            legal_refs=[
                {"regulation_type": "UU", "regulation_number": "13", "year": 2003}
            ]
        )
    )

    assert result["legal_ref_count"] == 0
    assert "legal_refs" in {m["field"] for m in result["missing"]}


def test_rujukan_kosong_dilaporkan():
    result = evaluate(FakeCase(legal_refs=[]))

    assert result["legal_ref_count"] == 0
    assert "legal_refs" in {m["field"] for m in result["missing"]}


def test_kriteria_jawaban_kosong_dilaporkan():
    result = evaluate(
        FakeCase(
            answer_criteria={
                "must_contain": [],
                "must_not_contain": [],
                "expected_conclusion": "",
            }
        )
    )

    assert "answer_criteria" in {m["field"] for m in result["missing"]}


def test_hanya_must_not_contain_sudah_dihitung_sebagai_kriteria():
    result = evaluate(
        FakeCase(
            answer_criteria={
                "must_contain": [],
                "must_not_contain": ["ganti rugi otomatis"],
                "expected_conclusion": "",
            }
        )
    )

    assert "answer_criteria" not in {m["field"] for m in result["missing"]}


def test_tanpa_jebakan_belum_siap_review():
    result = evaluate(FakeCase(traps=[]))

    assert result["trap_count"] == 0
    assert result["is_complete"] is False
    assert result["ready_for_review"] is False
    assert "traps" in {m["field"] for m in result["missing"]}


def test_split_tag_kosong_dilaporkan():
    result = evaluate(FakeCase(split_tag=None))

    assert "split_tag" in {m["field"] for m in result["missing"]}


def test_draft_kosong_melaporkan_semua_yang_kurang():
    result = evaluate(
        FakeCase(
            title=None,
            question=None,
            category=None,
            legal_refs=None,
            answer_criteria=None,
            traps=None,
            split_tag=None,
        )
    )

    assert result["is_complete"] is False
    assert {m["field"] for m in result["missing"]} == {
        "identity.title",
        "identity.question",
        "identity.category",
        "legal_refs",
        "answer_criteria",
        "traps",
        "split_tag",
    }
    assert all(m["message"] for m in result["missing"])