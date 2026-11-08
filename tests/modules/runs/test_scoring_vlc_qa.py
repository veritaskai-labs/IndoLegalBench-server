"""SCRUM-229: regression tests using client VLC reference answers (IDs 11–15).

Important: the sheet supplies reference answers and expected legal sources,
NOT actual model responses or client-approved numerical scores.
These tests verify deterministic citation matching against reference answers.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.legal_refs.seed import load_regulation_types
from app.modules.legal_refs.service import TypeName
from app.modules.runs.citation_score import CitationScorer

FIXTURE_PATH = Path(__file__).with_name("scoring_vlc_examples.json")


def _load_vlc_examples():
    with FIXTURE_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _nilai_vlc(jawaban, acuan):
    jenis = {item.code: TypeName(item.label, item.singkatan) for item in load_regulation_types()}
    return CitationScorer(jenis).score(jawaban, acuan)


@pytest.mark.parametrize("example", _load_vlc_examples(), ids=lambda item: f"VLC-{item['id']}")
def test_citation_scoring_vlc_reference_answer(example):
    hasil = _nilai_vlc(example["answer"], example["legal_refs"])

    expected = example["expected_score"]
    assert hasil.score == (Decimal(expected) if expected is not None else None)
    assert hasil.reference_total == example["expected_reference_total"]
    assert hasil.reference_correct == example["expected_reference_correct"]

    if expected is None:
        # VLC-11 only cites KUHPerdata; the existing deterministic
        # matching rules do not score free-text citations.
        assert hasil.ungradable_refs
        assert not hasil.regulation_matches
    else:
        assert all(match.matched for match in hasil.regulation_matches)
