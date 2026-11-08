"""Adapter penilai dari produk AI otomatis. PBI-14 AC1, AC4, SCRUM-258, SCRUM-271 (dipakai SCRUM-224).

judge.run_judge (SCRUM-224, #108) menerima `asker` apa pun yang punya ask(prompt),
dan JudgePrices dengan harga per juta token. build_judge membangun keduanya dari
penilai yang tersimpan: adapter dari ProviderType yang sama dengan produk, dengan
kredensial yang dibuka hanya di modul providers (crypto.py), dibungkus
SafeProviderAdapter supaya kredensial dan endpoint tidak masuk bukti jawaban.
Kurs harian tetap urusan pemanggil, jadi providers tidak bergantung pada runs.

Aktif atau tidaknya penilai dilaporkan, tidak ditolak di sini: sesi lama tetap
memakai penilai yang dipilih saat dibuat, dan aturan "boleh menilai dengan penilai
nonaktif" milik subtask penyambungan ke alur Nilai (keputusan PO 7 Nov).

Isolasi: SQLite (db_session), kunci enkripsi lewat monkeypatch env, kelas adapter
diganti lewat monkeypatch dispatch._ADAPTERS supaya tidak ada panggilan jaringan.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.modules.providers import judge_service
from app.modules.providers.adapters import dispatch
from app.modules.providers.adapters.base import ProviderResponse
from app.modules.providers.crypto import encrypt_credential
from app.modules.providers.models import MeasurementType
from app.shared.config import get_settings
from app.shared.exceptions import InternalError, NotFoundError
from tests.judge_rows import tambah_penilai
from tests.modules.conftest import USER_ID_QA

_SECRET = "judge-adapter-secret-3kRm"  # pragma: allowlist secret
_URL = "https://judge.private.example/v1/chat/completions"


@pytest.fixture
def kunci(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def adapter_palsu(monkeypatch):
    """Adapter tanpa jaringan yang mencatat argumen pembuatnya dan membocorkan rahasia
    di jawabannya, supaya scrub-nya terbukti."""
    dibuat: list[dict] = []

    class Palsu:
        def __init__(self, **kwargs):
            dibuat.append(kwargs)

        def ask(self, prompt, **kwargs):
            return ProviderResponse(
                text=f"{_SECRET} jawaban",
                model_version="judge-model-1",
                latency_ms=3,
                raw={"model": "judge-model-1", "echo": [_SECRET, _URL]},
                error=None,
            )

    monkeypatch.setitem(dispatch._ADAPTERS, "anthropic_messages", Palsu)
    return dibuat


def _penilai(db_session, **ubah):
    nilai = {
        "provider_type": "anthropic_messages",
        "base_url": _URL,
        "model_name": "judge-model-1",
        "credential_encrypted": encrypt_credential(_SECRET),
        "price_input_usd_per_mtok": Decimal("3.0000"),
        "price_output_usd_per_mtok": Decimal("15.0000"),
        "monthly_budget_idr": Decimal("250000.00"),
    } | ubah
    return tambah_penilai(db_session, USER_ID_QA, **nilai)


def test_penilai_dibangun_dari_baris_dengan_harga_dan_anggarannya(db_session, kunci, adapter_palsu):
    """Positif: adapter memakai jenis, URL, model, dan kredensial produk yang dipilih; harga
    dan anggaran bulanan produk itu ikut (SCRUM-271: tidak ada anggaran penilai terpisah)."""
    penilai = _penilai(db_session)

    hasil = judge_service.build_judge(db_session, penilai.id)

    assert adapter_palsu == [{"base_url": _URL, "model_name": "judge-model-1", "api_key": _SECRET}]
    assert hasil.judge_provider_id == penilai.id
    assert hasil.price_input_usd_per_mtok == Decimal("3.0000")
    assert hasil.price_output_usd_per_mtok == Decimal("15.0000")
    assert hasil.monthly_budget_idr == Decimal("250000.00")
    assert hasil.is_active is True


def test_jawaban_penilai_tanpa_kredensial_dan_endpoint(db_session, kunci, adapter_palsu):
    """Negatif: L5, bukti jawaban penilai (raw_response di llm_judge_results) bersih."""
    hasil = judge_service.build_judge(db_session, _penilai(db_session).id)

    jawaban = hasil.asker.ask("prompt penilai")

    assert _SECRET not in repr(jawaban)
    assert _URL not in repr(jawaban)
    assert _SECRET not in repr(hasil)


def test_penilai_nonaktif_tetap_dibangun_dengan_penandanya(db_session, kunci, adapter_palsu):
    """Edge: sesi lama memilih penilai yang kemudian dinonaktifkan; keputusannya milik
    pemanggil, jadi di sini hanya dilaporkan."""
    hasil = judge_service.build_judge(db_session, _penilai(db_session, is_active=False).id)

    assert hasil.is_active is False


def test_penilai_tidak_dikenal_404(db_session, kunci):
    with pytest.raises(NotFoundError) as galat:
        judge_service.build_judge(db_session, uuid.uuid4())

    assert galat.value.code == "JUDGE_PROVIDER_NOT_FOUND"


def test_kredensial_tidak_bisa_dibuka_menjadi_galat_internal_tanpa_rahasia(
    db_session, kunci, monkeypatch
):
    """Negatif: kunci enkripsi diganti; galatnya tidak memuat ciphertext atau plaintext."""
    penilai = _penilai(db_session)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    with pytest.raises(InternalError, match="judge credential unavailable") as galat:
        judge_service.build_judge(db_session, penilai.id)

    assert _SECRET not in str(galat.value)


def test_produk_manual_atau_terhapus_bukan_penilai_404(db_session, kunci):
    """Negatif: hanya produk otomatis yang belum dihapus yang bisa dibangun menjadi penilai."""
    # Arrange
    terhapus = _penilai(db_session, deleted_at=datetime(2026, 11, 1, tzinfo=UTC))
    manual = tambah_penilai(
        db_session,
        USER_ID_QA,
        measurement_type=MeasurementType.MANUAL,
        provider_type=None,
        base_url=None,
        model_name=None,
        credential_encrypted=None,
        credential_hint=None,
        rate_limit_per_minute=None,
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
        monthly_budget_idr=None,
        vendor_name="Justisio",
        site_url="https://justisio.test",
    )

    # Act + Assert
    for produk in (terhapus, manual):
        with pytest.raises(NotFoundError) as galat:
            judge_service.build_judge(db_session, produk.id)
        assert galat.value.code == "JUDGE_PROVIDER_NOT_FOUND"


def test_produk_tanpa_harga_tetap_dibangun_dengan_harga_kosong(db_session, kunci, adapter_palsu):
    """Edge: harga produk otomatis boleh kosong; penilai tetap bisa dipakai, harganya None."""
    # Arrange
    produk = _penilai(db_session, price_input_usd_per_mtok=None, price_output_usd_per_mtok=None)

    # Act
    hasil = judge_service.build_judge(db_session, produk.id)

    # Assert
    assert hasil.price_input_usd_per_mtok is None
    assert hasil.price_output_usd_per_mtok is None
    assert hasil.monthly_budget_idr == Decimal("250000.00")
