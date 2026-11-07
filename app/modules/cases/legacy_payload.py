"""Perlakuan payload lama yang masih membawa traps (SCRUM-256).

Klien menyatakan field jebakan tidak dipakai (PBI-14 AC4), jadi traps keluar
dari kontrak kasus aktif. Request lama tetap diterima: nilainya dibuang dan
tidak disimpan, dan server mencatat peringatan supaya perilakunya terlihat,
bukan kebetulan terabaikan. Isi traps tidak ditulis ke log.

Versi kasus yang sudah tersimpan tidak disentuh. Traps di JSON-nya tetap
ada sebagai riwayat, hanya tidak dibaca lagi.
"""

import logging
import uuid

from app.modules.cases.schemas import CaseWrite

logger = logging.getLogger(__name__)


def warn_if_traps_sent(payload: CaseWrite, *, case_id: uuid.UUID) -> bool:
    """Catat peringatan bila request mengirim traps. True kalau field itu dikirim."""
    if "traps" not in payload.model_fields_set:
        return False
    logger.warning(
        "Deprecated field 'traps' was sent and ignored on a case write "
        "(case_id=%s). It is not stored.",
        case_id,
    )
    return True
