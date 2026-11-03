"""Parameter model dari salinan sesi, dikirim apa adanya ke penyedia.

SCRUM-247 (revisi UAT klien PBI-23 AC2): parameter adalah JSON object bebas
yang ditulis engineer sesuai API modelnya, jadi tidak ada lagi pemetaan per
penyedia di sini. Kunci yang diatur adaptor (model, prompt) sudah ditolak saat
produk disimpan, dan adaptor tetap menulis kuncinya sendiri paling akhir.
Anthropic Messages menolak body tanpa max_tokens, jadi kalau tidak diisi
dipakai ANTHROPIC_DEFAULT_MAX_TOKENS. Penyedia lain tidak dibatasi.
"""

import copy
from collections.abc import Mapping
from typing import Any

ANTHROPIC_DEFAULT_MAX_TOKENS = 8192
ASK_TIMEOUT_SECONDS = 240.0

# Parameter model. None atau {} berarti pakai default penyedia.
ModelParameters = Mapping[str, Any]


def build_request_extras(provider_type: str, params: ModelParameters | None) -> dict:
    """Salinan dalam parameter untuk body request, ditambah max_tokens wajib Anthropic."""
    extras = copy.deepcopy(dict(params or {}))
    if provider_type == "anthropic_messages":
        extras.setdefault("max_tokens", ANTHROPIC_DEFAULT_MAX_TOKENS)
    return extras
