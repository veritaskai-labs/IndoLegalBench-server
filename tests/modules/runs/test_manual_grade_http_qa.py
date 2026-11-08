"""QA penilaian sesi manual dan peringatan rentang tanggal. PBI-22 (SCRUM-176) AC6, AC9, AC13.

SCRUM-238, lewat HTTP: tombol Nilai (POST /measurements/{id}/grade) dan halaman hasil
(GET /measurements/{id}/results) untuk sesi manual, ditambah halaman sesi manual untuk AC13.

Keputusan PO (8 Nov):
- AC6: "Platform menolak menjawab" mengikuti PBI-14 AC4, yaitu gangguan teknis yang tidak
  dinilai dan tidak mengurangi skor. Kasus yang sebagian percobaannya menolak dinilai dari
  percobaan lain.
- AC13: angka hari belum ada dari klien; QA memakai ambang sementara 1 hari.

Desain tes: decision table untuk syarat Nilai (status x arsip), state transition Lengkap ->
Dinilai -> Selesai, dan boundary value untuk ambang hari (tepat 1 hari tidak memicu, lewat
satu detik memicu).

Label: Positif = diterima, Negatif = ditolak, Edge = batas perilaku.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.main import app
from app.modules.grading.models import RubricVersion
from app.modules.runs import session_grade
from app.shared.config import get_settings
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN
from tests.modules.runs.manual_qa import URL, baca, buat_sesi, isi, isi_semua, orang, slot

AWAL = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
NORMAL = "Menurut Pasal 151 UU 13/2003, PHK tanpa surat tidak sah."
TEKS_TOLAK = (
    "LegalHero: pertanyaan ini di luar cakupan layanan kami. Silakan berkonsultasi dengan advokat."
)


class PenilaiUji:
    """Penilai LLM palsu: kriteria terpenuhi bila teksnya ada di jawaban (pola _JudgeGema SCRUM-233)."""

    error = None
    failure_kind = None
    input_tokens = 1
    output_tokens = 1
    text = ""

    def ask(self, prompt: str):
        kriteria = prompt.split("Criteria:\n", 1)[1].split("\n\nGround truth:", 1)[0]
        jawaban = prompt.split("Provider answer:\n", 1)[1].lower()
        baris = [b.strip() for b in kriteria.splitlines() if b.strip()]
        self.text = "".join(
            f"CRITERION: {b}\nMET: {'yes' if self._ada(b, jawaban) else 'no'}\n" for b in baris
        )
        return self

    @staticmethod
    def _ada(kriteria: str, jawaban: str) -> bool:
        return kriteria.removeprefix("Conclusion: ").lower() in jawaban


@pytest.fixture
def rubrik(siap_manual, monkeypatch):
    """Satu versi rubrik aktif dan penilai uji, supaya Nilai menghasilkan skor (#128: run tanpa
    satu pun skor gagal dan sesi tetap Lengkap). Sesi manual wajib punya provider penilai sejak
    #128, tapi provider itu tidak bisa dibangun di lingkungan tes, jadi penyelesainya diganti.
    Sejak #129 penyelesai mengembalikan (asker, galat, ikatan); tanpa ikatan biaya penilai tidak
    dihitung, sama seperti asker yang disuntik."""
    monkeypatch.setattr(
        session_grade,
        "_resolve_active_judge_asker",
        lambda db, session, judge_asker: (judge_asker or PenilaiUji(), None, None),
    )
    siap_manual.db.add(
        RubricVersion(
            version_no=1,
            effective_date=date.today(),
            is_active=True,
            locked_at=datetime.now(UTC),
            judge_prompt_quality="Prompt kualitas",
            judge_prompt_format="Prompt format",
            created_by=ADMIN_LAIN,
        )
    )
    siap_manual.db.commit()
    return siap_manual


@pytest.fixture
def setting(monkeypatch):
    def ganti(**nilai):
        baru = get_settings().model_copy(update=nilai)
        monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: baru)

    return ganti


def nilai(siap, sesi_id):
    return orang(siap, Role.ADMIN, "Admin Satu").post(f"/measurements/{sesi_id}/grade", json={})


def hasil(siap, sesi_id):
    respons = orang(siap, Role.ADMIN, "Admin Satu").get(f"/measurements/{sesi_id}/results")
    assert respons.status_code == 200, respons.text
    return respons.json()


def test_nilai_ditolak_selama_masih_ada_jawaban_kosong(rubrik):
    """Negatif: AC9, Nilai baru tersedia setelah semua kasus dan percobaan terisi."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)
    isi(rubrik, sesi["id"], slot(sesi, 0), NORMAL, oleh="Rina")

    # Act
    respons = nilai(rubrik, sesi["id"])

    # Assert
    assert (respons.status_code, respons.json()["code"]) == (409, "NOT_COMPLETE")
    assert baca(rubrik, sesi["id"])["status"] == "partially_filled"


def test_nilai_sesi_lengkap_menjadi_dinilai_lalu_bisa_ditandai_selesai(rubrik):
    """Positif: AC9 + AC8, Lengkap -> Dinilai saat Admin klik Nilai, lalu Dinilai -> Selesai."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)
    isi_semua(rubrik, sesi["id"])

    # Act
    dinilai = nilai(rubrik, sesi["id"])
    status_sesudah_nilai = baca(rubrik, sesi["id"])["status"]
    selesai = orang(rubrik, Role.ADMIN, "Admin Satu").post(f"{URL}/{sesi['id']}/finalize")

    # Assert
    assert dinilai.status_code == 200, dinilai.text
    assert status_sesudah_nilai == "graded"
    assert (selesai.status_code, selesai.json()["status"]) == (200, "finalized")


def test_penilai_tidak_tersedia_nilai_gagal_lalu_bisa_dinilai_lagi(rubrik, monkeypatch):
    """Negatif lalu Positif: AC9 x PBI-14 AC4 (#128). Tanpa penilai tidak ada satu pun skor, jadi
    run itu gagal (status failed, bukan hasil terbaru) dan sesi tetap Lengkap. /results tetap
    mengembalikan run gagal itu supaya alasannya terbaca (pilih_run_terbaru). Begitu penilai
    tersedia, Nilai berikutnya berhasil dan sesi menjadi Dinilai."""
    # Arrange
    tanpa_penilai = monkeypatch.context()
    with tanpa_penilai as m:
        m.setattr(
            session_grade,
            "_resolve_active_judge_asker",
            lambda db, session, judge_asker: (None, "judge_unavailable", None),
        )
        sesi = buat_sesi(rubrik, repeat_count=1)
        isi_semua(rubrik, sesi["id"])

        # Act: Nilai pertama tanpa penilai
        gagal = nilai(rubrik, sesi["id"])
        dibaca_gagal = hasil(rubrik, sesi["id"])
        status_sesudah_gagal = baca(rubrik, sesi["id"])["status"]

    # Act: Nilai kedua dengan penilai uji dari fixture
    berhasil = nilai(rubrik, sesi["id"])

    # Assert
    assert gagal.status_code == 200, gagal.text
    assert (gagal.json()["status"], gagal.json()["is_latest"]) == ("failed", False)
    assert (dibaca_gagal["status"], dibaca_gagal["is_latest"]) == ("failed", False)
    assert status_sesudah_gagal == "complete"
    assert (berhasil.json()["status"], berhasil.json()["is_latest"]) == ("completed", True)
    assert baca(rubrik, sesi["id"])["status"] == "graded"


def test_hasil_penilaian_sesi_manual_berlabel_manual(rubrik):
    """Positif: AC9, jawaban dan hasil penilaian manual selalu berlabel manual."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)
    isi_semua(rubrik, sesi["id"])
    assert nilai(rubrik, sesi["id"]).status_code == 200

    # Act
    dibaca = hasil(rubrik, sesi["id"])

    # Assert
    sumber = {a["source"] for c in dibaca["cases"] for a in c["attempts"]}
    assert sumber == {"manual"}


def test_nilai_tidak_berjalan_otomatis_saat_sesi_lengkap(rubrik):
    """Negatif: AC9 'penilaian tidak otomatis', sesi Lengkap belum punya hasil sebelum Nilai."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)

    # Act
    isi_semua(rubrik, sesi["id"])
    respons = orang(rubrik, Role.ADMIN, "Admin Satu").get(f"/measurements/{sesi['id']}/results")

    # Assert
    assert baca(rubrik, sesi["id"])["status"] == "complete"
    assert (respons.status_code, respons.json()["code"]) == (404, "GRADING_NOT_FOUND")


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_hanya_admin_yang_menilai_sesi_manual(rubrik, peran):
    """Negatif: AC9, tombol Nilai milik Admin; peran lain 403 dan status tetap Lengkap."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)
    isi_semua(rubrik, sesi["id"])

    # Act
    respons = orang(rubrik, peran, f"QA {peran.value}").post(
        f"/measurements/{sesi['id']}/grade", json={}
    )

    # Assert
    assert respons.status_code == 403
    assert baca(rubrik, sesi["id"])["status"] == "complete"


@pytest.mark.parametrize(
    ("selisih", "peringatan"),
    [
        pytest.param(timedelta(hours=23), False, id="23 jam: di bawah ambang"),
        pytest.param(timedelta(days=1), False, id="tepat 1 hari: belum lewat ambang"),
        pytest.param(timedelta(days=1, seconds=1), True, id="1 hari + 1 detik: lewat ambang"),
    ],
)
def test_peringatan_rentang_tanggal_mengikuti_ambang_hari(
    siap_manual, setting, selisih, peringatan
):
    """Boundary: AC13 dengan ambang sementara 1 hari (keputusan PO, angka final dari klien).
    Selisih dihitung dari waktu jawaban paling awal sampai paling akhir di satu sesi."""
    # Arrange
    setting(manual_answer_date_warning_days=1)
    sesi = buat_sesi(siap_manual, repeat_count=1)
    isi(siap_manual, sesi["id"], slot(sesi, 0), NORMAL, oleh="Rina", obtained_at=AWAL.isoformat())
    isi(
        siap_manual,
        sesi["id"],
        slot(sesi, 1),
        NORMAL,
        oleh="Budi",
        obtained_at=(AWAL + selisih).isoformat(),
    )

    # Act
    detail = baca(siap_manual, sesi["id"])

    # Assert
    assert detail["date_range_warning"] is peringatan
    assert detail["answer_date_from"] is not None
    assert detail["answer_date_to"] is not None


def test_peringatan_rentang_tanggal_mati_selama_angka_belum_ada(siap_manual, setting):
    """Edge: AC13, tanpa angka dari klien peringatan tidak pernah muncul, walau selisihnya jauh."""
    # Arrange
    setting(manual_answer_date_warning_days=None)
    sesi = buat_sesi(siap_manual, repeat_count=1)
    isi(siap_manual, sesi["id"], slot(sesi, 0), NORMAL, oleh="Rina", obtained_at=AWAL.isoformat())
    isi(
        siap_manual,
        sesi["id"],
        slot(sesi, 1),
        NORMAL,
        oleh="Budi",
        obtained_at=(AWAL + timedelta(days=60)).isoformat(),
    )

    # Act
    detail = baca(siap_manual, sesi["id"])

    # Assert
    assert detail["date_range_warning"] is False


def test_kasus_yang_semua_percobaannya_menolak_tetap_tampil_tidak_dinilai(rubrik):
    """Positif: AC6 + PBI-14 AC4 (keputusan PO), kasus yang semua percobaannya 'Platform menolak
    menjawab' tidak hilang dari hasil: tetap ada, tanpa skor, dan tiap percobaan gangguan teknis."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=1)
    tempat = slot(sesi, 0)
    tolak = isi(
        rubrik,
        sesi["id"],
        tempat,
        None,
        oleh="Rina",
        platform_refused=True,
        note="Diblokir filter konten",
    )
    assert tolak.status_code == 200, tolak.text
    isi_semua(rubrik, sesi["id"])
    assert nilai(rubrik, sesi["id"]).status_code == 200

    # Act
    dibaca = hasil(rubrik, sesi["id"])

    # Assert
    kasus = next(c for c in dibaca["cases"] if c["snapshot_item_id"] == tempat["snapshot_item_id"])
    assert kasus["scores"] is None
    assert [a["is_technical_disruption"] for a in kasus["attempts"]] == [True]


def test_penolakan_dengan_teks_dari_platform_tetap_tidak_dinilai(rubrik):
    """Regresi AC6 + PBI-14 AC4 (dilaporkan di #121, 8 Nov): tim legal mencentang 'Platform
    menolak menjawab' sambil menempel teks penolakan dari platform. Percobaan itu tetap gangguan
    teknis, dan kasusnya dinilai dari percobaan lain saja."""
    # Arrange
    sesi = buat_sesi(rubrik, repeat_count=2)
    pertama, kedua = slot(sesi, 0, 1), slot(sesi, 0, 2)
    isi(
        rubrik,
        sesi["id"],
        pertama,
        TEKS_TOLAK,
        oleh="Rina",
        platform_refused=True,
        note="Platform menolak, teks ditempel",
    )
    isi(rubrik, sesi["id"], kedua, NORMAL, oleh="Rina")
    isi_semua(rubrik, sesi["id"])
    assert nilai(rubrik, sesi["id"]).status_code == 200

    # Act
    dibaca = hasil(rubrik, sesi["id"])

    # Assert
    kasus = next(c for c in dibaca["cases"] if c["snapshot_item_id"] == pertama["snapshot_item_id"])
    percobaan = {a["repetition_no"]: a for a in kasus["attempts"]}
    assert percobaan[1]["is_technical_disruption"] is True
    assert percobaan[1]["score"] is None
    assert kasus["scores"] == percobaan[2]["score"]
