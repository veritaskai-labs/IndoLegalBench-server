"""Katalog jenis peraturan per request (PBI-21, SCRUM-160).

Satu query untuk semua jenis, aktif dan nonaktif: dipakai untuk memeriksa
jenis saat menyimpan dan untuk membaca rujukan Sprint 1 dari content.
Data dari fixture jenis_peraturan (seed SCRUM-159).
"""

from sqlalchemy import update

from app.modules.legal_refs import service
from app.modules.legal_refs.models import RegulationType


def test_katalog_memuat_semua_jenis_dengan_status_aktif(db_session, jenis_peraturan):
    db_session.execute(
        update(RegulationType).where(RegulationType.code == "permen").values(is_active=False)
    )
    db_session.commit()

    katalog = service.load_catalog(db_session)

    assert len(katalog.active) == 266
    assert katalog.active["uu"] is True
    assert katalog.active["permen"] is False
    assert katalog.names["uu"] == ("Undang-undang", "UU")
    assert katalog.names["ketetapan_mpr"] == ("Ketetapan MPR", None)


def test_katalog_membaca_rujukan_sprint_1_dengan_daftar_asli(db_session, jenis_peraturan):
    """Teks jenis lama "UU" dicocokkan ke code uu lewat singkatan, seperti migrasi 159."""
    lama = [{"regulation_type": "UU", "regulation_number": "13", "year": 2003, "pasal": "156"}]

    hasil = service.load_catalog(db_session).citations(lama)

    assert hasil[0]["kind"] == "structured"
    assert hasil[0]["links"][0]["regulation_type"] == "uu"


def test_katalog_kosong_tanpa_jenis(db_session):
    katalog = service.load_catalog(db_session)

    assert katalog.active == {}
    assert (
        katalog.citations([{"regulation_type": "UU", "regulation_number": "13"}])[0]["kind"]
        == "free_text"
    )
