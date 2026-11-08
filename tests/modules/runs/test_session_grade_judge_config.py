"""Penilai LLM yang tidak terpasang pada sesi. SCRUM-78 SCRUM-269.

PBI-14 AC4: penilai yang tidak tersedia adalah gangguan teknis yang tercatat dengan
alasan jelas, bukan percobaan yang diam-diam dilewati tanpa skor kualitas dan format.

Label: Positif = penilai terpasang, Negatif = penilai hilang atau gagal dimuat.
"""

import logging
import uuid
from unittest.mock import MagicMock

import pytest

from app.modules.runs import session_grade
from app.modules.runs.session_grade import grade_session
from app.shared.security import Role
from tests.modules.runs.test_session_grade import (  # noqa: F401  (fixture)
    _setup_sesi_lengkap,
    pembuat,
    produk,
)


def _sesi(judge_provider_id):
    sesi = MagicMock()
    sesi.judge_provider_id = judge_provider_id
    return sesi


def test_asker_dari_pemanggil_dipakai_apa_adanya():
    """Positif: asker yang diberikan (mis. uji) menggantikan pemuatan dari database."""
    # Arrange
    asker = object()

    # Act
    hasil, galat, ikatan = session_grade._resolve_active_judge_asker(
        MagicMock(), _sesi(None), asker
    )

    # Assert
    assert hasil is asker
    assert galat is None
    assert ikatan is None


def test_sesi_tanpa_penilai_ditandai_judge_not_configured():
    """Negatif: sesi lama dengan judge_provider_id kosong tidak dilewati diam-diam."""
    # Arrange
    sesi = _sesi(None)

    # Act
    hasil, galat, ikatan = session_grade._resolve_active_judge_asker(MagicMock(), sesi, None)

    # Assert
    assert hasil is None
    assert galat == "judge_not_configured"
    assert ikatan is None


def test_penilai_gagal_dimuat_ditandai_judge_unavailable_dan_dicatat_di_log(monkeypatch, caplog):
    """Negatif: galat build_judge dicatat tanpa membocorkan isinya ke hasil penilaian."""

    # Arrange
    def gagal(db, judge_id):
        raise RuntimeError("kredensial rusak")

    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", gagal)
    sesi = _sesi(uuid.uuid4())

    # Act
    with caplog.at_level(logging.ERROR, logger=session_grade.__name__):
        hasil, galat, ikatan = session_grade._resolve_active_judge_asker(MagicMock(), sesi, None)

    # Assert
    assert hasil is None
    assert galat == "judge_unavailable"
    assert ikatan is None
    assert any(str(sesi.judge_provider_id) in rekam.getMessage() for rekam in caplog.records)
    assert all("kredensial rusak" not in rekam.getMessage() for rekam in caplog.records)


def test_penilai_terdaftar_dimuat_dari_database(monkeypatch):
    """Positif: penilai terdaftar dibangun lewat judge_service dan asker-nya dipakai."""
    # Arrange
    ikatan = MagicMock()
    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", lambda db, j: ikatan)

    # Act
    hasil, galat, dimuat = session_grade._resolve_active_judge_asker(
        MagicMock(), _sesi(uuid.uuid4()), None
    )

    # Assert
    assert hasil is ikatan.asker
    assert galat is None
    assert dimuat is ikatan


def test_grade_sesi_tanpa_penilai_menandai_percobaan_gangguan_teknis(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Negatif: percobaan tidak diberi skor dan alasannya terbaca di penjelasan."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat)
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    hasil = grade_session(db_session, sesi.id, actor_id=pembuat.id, actor_role=Role.ADMIN)

    # Assert
    percobaan = hasil.cases[0].attempts[0]
    assert percobaan.is_technical_disruption is True
    assert percobaan.score is None
    assert percobaan.explanation == "judge_not_configured"


@pytest.mark.parametrize("galat", ["judge_not_configured", "judge_unavailable"])
def test_galat_penilai_sampai_ke_semua_percobaan(galat):
    """Edge: kode galat penilai dipasang ke setiap percobaan di setiap kasus."""
    # Arrange
    percobaan = [MagicMock(judge_error=None), MagicMock(judge_error=None)]
    kasus = [MagicMock(attempts=percobaan[:1]), MagicMock(attempts=percobaan[1:])]

    # Act
    session_grade._execute_judge_for_all_cases(None, galat, kasus)

    # Assert
    assert [p.judge_error for p in percobaan] == [galat, galat]
