"""Convert stored model parameters to the JSON each provider receives.

Revision ID: b7e2c9d41a58
Revises: d3c8a1e54b20
Create Date: 2026-11-02 21:30:00.000000

PBI-23 SCRUM-177 (SCRUM-247, revisi UAT klien AC2):
- Sebelumnya model_params berbentuk generik (temperature, effort, thinking, web_search)
  dan adaptor menerjemahkannya per penyedia. Sekarang JSON dikirim apa adanya.
- Upgrade mengubah tiap baris menjadi body yang dulu benar-benar dikirim, supaya
  pengukuran berikutnya memakai parameter yang sama. Kunci yang dulu tidak pernah
  dikirim (thinking, web_search, effort selain OpenAI) dibuang dan dilaporkan.
- Downgrade mengembalikan bentuk generik; kunci yang tidak muat di skema lama
  (extra=forbid) dibuang dan dilaporkan.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c9d41a58"  # pragma: allowlist secret
down_revision: str | None = "d3c8a1e54b20"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_EFFORTS = ("low", "medium", "high")

# Hasil pemetaan: (JSON baru atau None bila kosong, kunci yang dibuang, terurut).
Hasil = tuple[dict | None, list[str]]


def _suhu_lama(nilai: Any) -> bool:
    """Temperature yang diterima skema lama: angka 0 sampai 2."""
    return isinstance(nilai, int | float) and not isinstance(nilai, bool) and 0 <= nilai <= 2


def ke_json_penyedia(penyedia: str | None, lama: Mapping[str, Any] | None) -> Hasil:
    """Body yang dulu dihasilkan adapters/params.py SCRUM-208 untuk parameter ini."""
    baru: dict = {}
    dibuang: list[str] = []
    for kunci, nilai in (lama or {}).items():
        if nilai is None:
            continue
        if kunci == "temperature" and penyedia == "gemini_interactions":
            baru["generation_config"] = {"temperature": nilai}
        elif kunci == "temperature" and penyedia in ("openai_compatible", "anthropic_messages"):
            baru["temperature"] = nilai
        elif kunci == "effort" and penyedia == "openai_compatible" and nilai in _EFFORTS:
            baru["reasoning_effort"] = nilai
        else:
            dibuang.append(kunci)
    return baru or None, sorted(dibuang)


def ke_bentuk_lama(penyedia: str | None, baru: Mapping[str, Any] | None) -> Hasil:
    """Kebalikan ke_json_penyedia; yang tidak punya padanan di skema lama dibuang."""
    lama: dict = {}
    dibuang: list[str] = []
    for kunci, nilai in (baru or {}).items():
        if kunci == "generation_config" and penyedia == "gemini_interactions":
            isi = nilai if isinstance(nilai, Mapping) else {}
            for anak, suhu in isi.items():
                if anak == "temperature" and _suhu_lama(suhu):
                    lama["temperature"] = suhu
                else:
                    dibuang.append(f"generation_config.{anak}")
            if not isinstance(nilai, Mapping):
                dibuang.append(kunci)
        elif (
            kunci == "temperature"
            and penyedia in ("openai_compatible", "anthropic_messages")
            and _suhu_lama(nilai)
        ):
            lama["temperature"] = nilai
        elif kunci == "reasoning_effort" and penyedia == "openai_compatible" and nilai in _EFFORTS:
            lama["effort"] = nilai
        else:
            dibuang.append(kunci)
    return lama or None, sorted(dibuang)


_SQL_PARAMETER = """
    SELECT id, CAST(provider_type AS text), model_params
    FROM ai_products
    WHERE model_params IS NOT NULL
    ORDER BY id
"""
_SQL_SIMPAN = sa.text("UPDATE ai_products SET model_params = :params WHERE id = :id").bindparams(
    sa.bindparam("params", type_=_JSONB)
)


def _ubah_semua(bind, petakan: Callable[[str | None, Mapping | None], Hasil], arah: str) -> None:
    jumlah = 0
    for product_id, penyedia, params in bind.execute(sa.text(_SQL_PARAMETER)).all():
        hasil, dibuang = petakan(penyedia, params)
        # Bindparam JSON menulis None sebagai JSON null, padahal C1 butuh SQL NULL.
        if hasil is None:
            bind.execute(
                sa.text("UPDATE ai_products SET model_params = NULL WHERE id = :id"),
                {"id": product_id},
            )
        else:
            bind.execute(_SQL_SIMPAN, {"id": product_id, "params": hasil})
        if dibuang:
            jumlah += 1
            log.warning(
                "SCRUM-247 %s: produk %s, kunci parameter dibuang: %s",
                arah,
                product_id,
                ", ".join(dibuang),
            )
    if jumlah:
        log.warning("SCRUM-247 %s: %d produk kehilangan kunci parameter", arah, jumlah)


def ubah_ke_json_penyedia(bind) -> None:
    _ubah_semua(bind, ke_json_penyedia, "upgrade")


def kembalikan_ke_bentuk_lama(bind) -> None:
    _ubah_semua(bind, ke_bentuk_lama, "downgrade")


def upgrade() -> None:
    ubah_ke_json_penyedia(op.get_bind())


def downgrade() -> None:
    kembalikan_ke_bentuk_lama(op.get_bind())
