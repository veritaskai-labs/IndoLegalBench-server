"""Baris judge_providers sintetis lewat ORM, untuk tes SQLite (SCRUM-258).

Ciphertext palsu cukup: tes ini tidak membuka kredensial. Nilai sintetis (L3, L5).
"""

import uuid
from decimal import Decimal

from app.modules.providers.models import JudgeProvider, ProviderType


def tambah_penilai(db, created_by: uuid.UUID, **ubah) -> JudgeProvider:
    nilai = {
        "name": f"Penilai uji {uuid.uuid4().hex[:8]}",
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://judge.example.test/v1/chat/completions",
        "model_name": "judge-model-1",
        "credential_encrypted": b"terenkripsi",
        "credential_hint": "7kQz",
        "price_input_usd_per_mtok": Decimal("0.2700"),
        "price_output_usd_per_mtok": Decimal("1.1000"),
        "monthly_budget_idr": Decimal("500000.00"),
        "created_by": created_by,
    } | ubah
    penilai = JudgeProvider(**nilai)
    db.add(penilai)
    db.commit()
    return penilai
