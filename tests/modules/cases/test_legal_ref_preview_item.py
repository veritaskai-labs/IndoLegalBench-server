"""Kontrak hasil pratinjau: tepat satu dari teks atau error (SCRUM-162)."""

import pytest
from pydantic import ValidationError

from app.modules.cases.schemas import LegalRefPreviewItem

ERROR = {
    "code": "FIELD_REQUIRED",
    "message": "Nomor peraturan wajib diisi.",
    "field": "legal_refs[0].links[0].number",
}


@pytest.mark.parametrize("text", ["Pasal 156 UU 13/2003", ""])
def test_teks_tanpa_error_diterima(text):
    assert LegalRefPreviewItem(display_text=text, error=None).display_text == text


def test_error_tanpa_teks_diterima():
    item = LegalRefPreviewItem(display_text=None, error=ERROR)
    assert item.error.model_dump() == ERROR


@pytest.mark.parametrize("text,error", [(None, None), ("Pasal 156 UU 13/2003", ERROR), ("", ERROR)])
def test_kedua_atau_tanpa_hasil_ditolak(text, error):
    with pytest.raises(ValidationError, match="Exactly one of display_text or error must be set"):
        LegalRefPreviewItem(display_text=text, error=error)
