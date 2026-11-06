"""Test pemetaan snapshot kasus untuk score aggregation. SCRUM-225."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases.models import SplitTag
from app.modules.runs.adapters import SnapshotCaseAdapter
from app.modules.runs.score_aggregation import (
    LAIN_LAIN,
    ScorePair,
    category_breakdown,
    three_scores,
)
from app.modules.runs.snapshot_cases import (
    CaseFacts,
    build_case_scores,
    case_facts_from_body,
)

SESI = uuid.UUID("11111111-1111-1111-1111-111111111111")
ITEM = uuid.UUID("22222222-2222-2222-2222-222222222222")


def body(**sections):
    isi = {
        "case_code": "PJK-001",
        "split_tag": "dev",
        "category": "ketenagakerjaan",
    }
    isi.update(sections)
    return {
        "version_no": 2,
        "status": "approved",
        "sections": isi,
    }


def fakta(**overrides):
    nilai = {
        "snapshot_item_id": ITEM,
        "case_code": "PJK-001",
        "split_tag": SplitTag.DEV,
        "category": "ketenagakerjaan",
    }
    nilai.update(overrides)
    return CaseFacts(**nilai)


def test_kode_tag_dan_kategori_dibaca_dari_snapshot():
    hasil = case_facts_from_body(ITEM, body())

    assert hasil == fakta()


def test_kasus_test_terbaca_sebagai_test():
    hasil = case_facts_from_body(
        ITEM,
        body(split_tag="test"),
    )

    assert hasil.split_tag is SplitTag.TEST


@pytest.mark.parametrize("kosong", [None, "", "   "])
def test_kategori_kosong_jadi_tanpa_kategori(kosong):
    hasil = case_facts_from_body(
        ITEM,
        body(category=kosong),
    )

    assert hasil.category is None


def test_kategori_berspasi_dirapikan():
    hasil = case_facts_from_body(
        ITEM,
        body(category="  pajak  "),
    )

    assert hasil.category == "pajak"


@pytest.mark.parametrize("mentah", [None, "", "both", "DEV"])
def test_split_tag_tidak_dikenal_ditolak(mentah):
    with pytest.raises(ValueError) as galat:
        case_facts_from_body(
            ITEM,
            body(split_tag=mentah),
        )

    assert str(ITEM) in str(galat.value)


def test_body_kosong_ditolak():
    with pytest.raises(ValueError):
        case_facts_from_body(ITEM, {})


def test_angka_dipasangkan_lewat_snapshot_item_id():
    lain = uuid.uuid4()

    daftar = [
        fakta(),
        fakta(
            snapshot_item_id=lain,
            case_code="PJK-002",
        ),
    ]

    angka = {
        lain: ScorePair(
            deterministic=Decimal("0.50"),
            judge=Decimal("3.00"),
        ),
        ITEM: ScorePair(
            deterministic=Decimal("1.00"),
            judge=Decimal("5.00"),
        ),
    }

    kasus = build_case_scores(daftar, angka)

    assert [item.case_code for item in kasus] == [
        "PJK-001",
        "PJK-002",
    ]
    assert kasus[0].scores.judge == Decimal("5.00")
    assert kasus[1].scores.judge == Decimal("3.00")


def test_kasus_belum_dinilai_tetap_dihitung():
    daftar = [
        fakta(),
        fakta(
            snapshot_item_id=uuid.uuid4(),
            category=None,
        ),
    ]

    kasus = build_case_scores(daftar, {})
    pecahan = category_breakdown(kasus)

    assert [
        (bagian.category, bagian.case_count)
        for bagian in pecahan
    ] == [
        ("ketenagakerjaan", 1),
        (LAIN_LAIN, 1),
    ]

    assert pecahan[0].scores == ScorePair(
        deterministic=None,
        judge=None,
    )


def test_dari_snapshot_sampai_tiga_skor():
    dev = uuid.uuid4()
    test = uuid.uuid4()

    daftar = [
        fakta(
            snapshot_item_id=dev,
            split_tag=SplitTag.DEV,
        ),
        fakta(
            snapshot_item_id=test,
            split_tag=SplitTag.TEST,
            case_code="PJK-002",
        ),
    ]

    angka = {
        dev: ScorePair(
            deterministic=Decimal("0.80"),
            judge=Decimal("5.00"),
        ),
        test: ScorePair(
            deterministic=Decimal("0.40"),
            judge=Decimal("3.00"),
        ),
    }

    tiga = three_scores(build_case_scores(daftar, angka))

    assert tiga.dev.judge == Decimal("5.00")
    assert tiga.test.judge == Decimal("3.00")
    assert tiga.combined.judge == Decimal("4.00")


def test_adapter_memetakan_item_snapshot():
    kedua = uuid.uuid4()
    db = MagicMock()

    with patch(
        "app.modules.runs.adapters.repository.snapshot_case_items",
        return_value=[
            (ITEM, body()),
            (
                kedua,
                body(
                    case_code="PJK-002",
                    split_tag="test",
                ),
            ),
        ],
    ) as query:
        hasil = SnapshotCaseAdapter(db).case_facts(SESI)

    query.assert_called_once_with(db, SESI)

    assert hasil == (
        fakta(),
        fakta(
            snapshot_item_id=kedua,
            case_code="PJK-002",
            split_tag=SplitTag.TEST,
        ),
    )


def test_sesi_tanpa_item_snapshot():
    db = MagicMock()

    with patch(
        "app.modules.runs.adapters.repository.snapshot_case_items",
        return_value=[],
    ):
        hasil = SnapshotCaseAdapter(db).case_facts(SESI)

    assert hasil == ()