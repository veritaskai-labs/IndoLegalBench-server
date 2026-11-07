"""Bantuan test untuk data traps lama (SCRUM-256), dipakai beberapa file test kasus."""

import uuid

from app.modules.cases.models import Case

TRAPS_LAMA = [
    {"description": "Mencampur upah dan pesangon", "expected_model_behavior": "menolak"},
    {"description": "Menyebut pasal yang dicabut", "expected_model_behavior": None},
]


def tanam_traps_lama(db_session, case_id: str, traps: list) -> None:
    """Simulasikan versi yang disimpan sebelum traps dicabut."""
    kasus = db_session.get(Case, uuid.UUID(case_id))
    versi = kasus.current_version
    versi.content = {**versi.content, "traps": traps}
    db_session.commit()
