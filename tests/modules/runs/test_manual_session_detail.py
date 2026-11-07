"""GET /manual-measurements/{id}: progres dan daftar kasus sesi manual. PBI-22 (SCRUM-176).

AC8: progres "X dari N jawaban terisi", daftar kasus dengan tanda sudah atau belum terisi,
dan filter "belum terisi"; X dan N selalu untuk seluruh sesi, walau daftarnya disaring.
AC11: semua Admin dan Kontributor boleh melihat sesi manual milik orang lain.
AC12: jumlah percobaan ikut, untuk penanda "jumlah percobaan berbeda" di PBI-15.
AC13: rentang tanggal jawaban dan peringatannya. Batas hari masih TBA dari klien, jadi
setting MANUAL_ANSWER_DATE_WARNING_DAYS kosong secara bawaan dan peringatan mati.

Pertanyaan kasus ikut di tiap kasus, untuk tombol "Salin pertanyaan" (AC3, SCRUM-235).

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.main import app
from app.modules.runs.models import Attempt, MeasurementSession, SessionType
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.runs.test_manual_session_endpoint import URL, _badan

HARI_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
HARI_8 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def _sesi_manual(siap_manual, **ubah) -> uuid.UUID:
    respons = siap_manual.sebagai(Role.ADMIN).post(URL, json=_badan(siap_manual, **ubah))
    assert respons.status_code == 201, respons.text
    return uuid.UUID(respons.json()["session_id"])


def _isi_kasus(db, sesi_id, nomor_kasus: int, diperoleh: datetime) -> uuid.UUID:
    """Isi semua percobaan kasus ke-n (urut id item), seperti SCRUM-231 menyimpan jawaban."""
    item_id = sorted({a.snapshot_item_id for a in _percobaan(db, sesi_id)})[nomor_kasus]
    db.execute(
        update(Attempt)
        .where(Attempt.session_id == sesi_id, Attempt.snapshot_item_id == item_id)
        .values(filled_at=diperoleh, answered_at_platform=diperoleh)
    )
    db.commit()
    return item_id


def _percobaan(db, sesi_id) -> list[Attempt]:
    return list(db.scalars(select(Attempt).where(Attempt.session_id == sesi_id)))


def _rentang(db, sesi_id, dari, sampai) -> None:
    db.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(answer_date_from=dari, answer_date_to=sampai)
    )
    db.commit()


@pytest.fixture
def batas_hari():
    """Isi batas hari peringatan rentang tanggal (AC13) untuk satu tes."""

    def pasang(hari: int | None) -> None:
        app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
            update={"manual_answer_date_warning_days": hari}
        )

    yield pasang
    app.dependency_overrides.pop(get_settings, None)


def test_sesi_baru_menampilkan_semua_kasus_belum_terisi(siap_manual):
    """Positif: AC8, 0 dari 3 terisi; kasus urut kode dengan pertanyaan untuk disalin."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)

    # Act
    respons = siap_manual.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}")

    # Assert
    assert respons.status_code == 200, respons.text
    isi = respons.json()
    assert (isi["status"], isi["filled_answers"], isi["total_answers"]) == ("not_filled", 0, 3)
    assert (isi["product_name"], isi["vendor_name"], isi["site_url"]) == (
        siap_manual.produk.name,
        "LegalHero",
        "https://legalhero.id",
    )
    assert [kasus["case_code"] for kasus in isi["cases"]] == ["PHK-001", "PHK-002", "PHK-003"]
    assert all(kasus["question"] for kasus in isi["cases"])
    assert {kasus["filled"] for kasus in isi["cases"]} == {False}
    assert (isi["answer_date_from"], isi["answer_date_to"], isi["date_range_warning"]) == (
        None,
        None,
        False,
    )


def test_setiap_kasus_menampilkan_slot_per_percobaan(siap_manual):
    """Positif: AC4, AC12, satu kartu per kasus berisi Percobaan 1 dan 2, urut nomornya."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=2)

    # Act
    isi = siap_manual.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}").json()

    # Assert
    assert (isi["repetitions"], isi["total_answers"]) == (2, 6)
    assert [slot["repetition_no"] for slot in isi["cases"][0]["attempts"]] == [1, 2]


def test_kasus_terisi_ditandai_dan_progres_bertambah(siap_manual):
    """Positif: AC8, kasus dengan semua percobaan terisi ditandai sudah terisi."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    item_id = _isi_kasus(siap_manual.db, sesi_id, 0, HARI_1)

    # Act
    isi = siap_manual.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}").json()

    # Assert
    assert isi["filled_answers"] == 1
    terisi = [kasus for kasus in isi["cases"] if kasus["filled"]]
    assert [kasus["snapshot_item_id"] for kasus in terisi] == [str(item_id)]
    assert terisi[0]["attempts"][0]["filled"] is True


def test_filter_belum_terisi_menyaring_daftar_tanpa_mengubah_progres(siap_manual):
    """Positif: AC8, filter 'belum terisi' hanya menyaring kasus; X dari N tetap seluruh sesi."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual, repeat_count=1)
    item_id = _isi_kasus(siap_manual.db, sesi_id, 0, HARI_1)

    # Act
    isi = siap_manual.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}?unfilled=true").json()

    # Assert
    assert (isi["filled_answers"], isi["total_answers"]) == (1, 3)
    assert len(isi["cases"]) == 2
    assert str(item_id) not in {kasus["snapshot_item_id"] for kasus in isi["cases"]}


def test_kontributor_melihat_sesi_manual_milik_admin(siap_manual):
    """Positif: AC11, Kontributor boleh melihat sesi manual yang dibuat orang lain."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)

    # Act
    respons = siap_manual.sebagai(Role.CONTRIBUTOR).get(f"{URL}/{sesi_id}")

    # Assert
    assert respons.status_code == 200


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI])
def test_selain_admin_dan_kontributor_tidak_bisa_melihat(siap_manual, peran):
    """Negatif: AC11 hanya menyebut Admin dan Author/Kontributor."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)

    # Act
    respons = siap_manual.sebagai(peran).get(f"{URL}/{sesi_id}")

    # Assert
    assert respons.status_code == 403


def test_sesi_otomatis_atau_tidak_ada_ditolak_404(siap_manual):
    """Negatif: halaman ini hanya untuk sesi manual."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    siap_manual.db.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == sesi_id)
        .values(session_type=SessionType.AUTOMATIC)
    )
    siap_manual.db.commit()
    admin = siap_manual.sebagai(Role.ADMIN)

    # Act / Assert
    for target in (sesi_id, uuid.uuid4()):
        respons = admin.get(f"{URL}/{target}")
        assert respons.status_code == 404
        assert respons.json()["code"] == "MANUAL_SESSION_NOT_FOUND"


@pytest.mark.parametrize(
    ("hari", "peringatan"),
    [
        # Negatif: batas hari TBA dari klien; tanpa batas tidak ada peringatan.
        (None, False),
        # Positif: rentang 7 hari melebihi batas 2 hari.
        (2, True),
        # Edge: rentang 7 hari tepat di batas 7 hari belum melebihi.
        (7, False),
    ],
)
def test_peringatan_rentang_tanggal_mengikuti_setting(siap_manual, batas_hari, hari, peringatan):
    """Positif/Negatif/Edge: AC13, peringatan bila rentang tanggal jawaban melebihi batas."""
    # Arrange
    sesi_id = _sesi_manual(siap_manual)
    _rentang(siap_manual.db, sesi_id, HARI_1, HARI_8)
    batas_hari(hari)

    # Act
    isi = siap_manual.sebagai(Role.ADMIN).get(f"{URL}/{sesi_id}").json()

    # Assert
    assert isi["date_range_warning"] is peringatan
    assert isi["answer_date_from"].startswith("2026-11-01")
