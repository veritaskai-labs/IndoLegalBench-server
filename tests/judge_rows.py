"""Penilai sintetis untuk tes SQLite: produk AI otomatis lewat ORM (SCRUM-271).

Sejak PBI-14 penilai dipilih dari produk AI otomatis yang aktif, bukan dari registri
judge_providers. Ciphertext palsu cukup: tes ini tidak membuka kredensial. Nilai sintetis (L3, L5).
"""

import uuid
from decimal import Decimal

from app.modules.providers.models import AiProduct, MeasurementType, ProviderType


def tambah_penilai(db, created_by: uuid.UUID, **ubah) -> AiProduct:
    nilai = {
        "name": f"Penilai uji {uuid.uuid4().hex[:8]}",
        "measurement_type": MeasurementType.AUTOMATIC,
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://judge.example.test/v1/chat/completions",
        "model_name": "judge-model-1",
        "credential_encrypted": b"terenkripsi",
        "credential_hint": "7kQz",
        "rate_limit_per_minute": 30,
        "price_input_usd_per_mtok": Decimal("0.2700"),
        "price_output_usd_per_mtok": Decimal("1.1000"),
        "monthly_budget_idr": Decimal("500000.00"),
        "created_by": created_by,
    } | ubah
    penilai = AiProduct(**nilai)
    db.add(penilai)
    db.commit()
    return penilai
