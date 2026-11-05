"""QA hasil akhir sesi seperti yang dibaca lewat HTTP. SCRUM-76 SCRUM-213.

Kontrak hasil (PO 4 Nov, SCRUM-214 dan SCRUM-249): semua sukses finished; ada kegagalan
teknis, termasuk semua gagal, finished_with_failures; anggaran habis paused dengan
stop_reason budget dan hasil tersimpan tanpa notifikasi selesai; gagal sebelum mulai
failed. Tes runtime memeriksa baris database. Di sini yang diperiksa adalah apa yang
dibaca halaman Eksekusi: status dan penyebab berhenti di detail, progres di daftar, dan
notifikasi di kotak masuk pemulai.

Label: Positif = hasil sesuai kontrak, Edge = batas perilaku.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.shared.security import Role
from tests.modules.runs.test_execution_runtime import Provider, runner, start

URL = "/measurements"
NOTIFIKASI = "/notifications"
PERCOBAAN = 9  # fixture siap: 3 kasus approved x 3 pengulangan


def _baca(siap, sesi_id):
    klien = siap.sebagai(Role.ADMIN)
    detail = klien.get(f"{URL}/{sesi_id}").json()
    daftar = {item["id"]: item for item in klien.get(URL).json()}
    notifikasi = klien.get(NOTIFIKASI).json()
    return detail, daftar[str(sesi_id)], notifikasi


def _status_percobaan(detail):
    return [a["status"] for kasus in detail["cases"] for a in kasus["attempts"]]


@pytest.mark.parametrize(
    "perilaku,gagal",
    [("mixed", 1), ("error", PERCOBAAN)],
    ids=["sebagian-gagal", "semua-gagal"],
)
def test_ada_kegagalan_teknis_berakhir_finished_with_failures(siap, perilaku, gagal):
    """Positif: AC10 dan PO 4 Nov, kegagalan teknis (juga semua gagal) bukan failed dan bukan finished."""
    # Arrange
    sesi_id = start(siap)

    # Act
    assert runner(siap, Provider(perilaku))() is True
    siap.db.expire_all()
    detail, baris, notifikasi = _baca(siap, sesi_id)

    # Assert
    assert detail["status"] == "finished_with_failures"
    assert detail["stop_reason"] is None
    status = _status_percobaan(detail)
    assert status.count("technical_failure") == gagal
    assert status.count("succeeded") == PERCOBAAN - gagal
    for kasus in detail["cases"]:
        for percobaan in kasus["attempts"]:
            if percobaan["status"] == "technical_failure":
                assert percobaan["answer_text"] is None
    assert baris["progress"] == {"completed": PERCOBAAN, "total": PERCOBAAN}
    assert notifikasi["total"] == 1
    assert notifikasi["items"][0]["link"] == f"/runs/{sesi_id}"
    assert "gagal teknis" in notifikasi["items"][0]["body"]


def test_anggaran_habis_menjeda_sesi_dengan_hasil_tersimpan_tanpa_notifikasi(siap):
    """Positif: AC5, anggaran habis di tengah: paused + stop_reason budget, hasil tetap, tanpa notifikasi selesai."""
    # Arrange
    sesi_id = start(siap)
    jalan = runner(siap, Provider())
    siap.produk.monthly_budget_idr = Decimal("0.01")
    siap.db.commit()

    # Act
    assert jalan() is True
    siap.db.expire_all()
    detail, baris, notifikasi = _baca(siap, sesi_id)

    # Assert
    assert detail["status"] == "paused"
    assert detail["stop_reason"] == "budget"
    assert detail["stopped_at"] is not None
    assert detail["finished_at"] is None
    status = _status_percobaan(detail)
    assert status.count("succeeded") >= 1
    assert "technical_failure" not in status
    assert baris["progress"]["completed"] == status.count("succeeded")
    assert baris["progress"]["total"] == PERCOBAAN
    assert notifikasi["total"] == 0


def test_produk_dihapus_setelah_mulai_membuat_sesi_failed_dan_memberi_tahu_pemulai(siap):
    """Edge: gagal sebelum mulai: sesi failed tanpa percobaan terkirim, pemulai diberi tahu."""
    # Arrange
    sesi_id = start(siap)
    provider = Provider()
    jalan = runner(siap, provider)
    siap.produk.deleted_at = datetime.now(UTC)
    siap.db.commit()

    # Act
    jalan()
    siap.db.expire_all()
    detail, _, notifikasi = _baca(siap, sesi_id)

    # Assert
    assert detail["status"] == "failed"
    assert detail["stop_reason"] == "error"
    assert detail["finished_at"] is not None
    assert provider.calls == []
    assert notifikasi["total"] == 1
    assert notifikasi["items"][0]["link"] == f"/runs/{sesi_id}"
    assert notifikasi["items"][0]["type"] == "measurement_failed"
