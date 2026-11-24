"""Unduh berkas skor satu sesi: kelayakan, isi per peran, dan penyimpanan. PBI-16 SCRUM-327.

AC5 (isi), AC6 (hanya sesi layak, selain itu ditolak dengan alasan), AC7 (berkas disimpan).
Sumber data dan penyimpanan diganti tiruan; tanpa database.
Review PR #152: B1 (berkas tidak boleh kosong atau kehilangan kasus) dan B2 (kontrak
diterima apa adanya tanpa penilaian).
"""

import csv
import io
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.modules.cases.models import SplitTag
from app.modules.cases.service import MASKED_CASE_CODE
from app.modules.reports.comparison_rules import (
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_FINISHED,
    SESSION_NOT_GRADED,
    SESSION_RUN_FAILED,
)
from app.modules.reports.score_files import (
    NO_VISIBLE_CASES,
    SESSION_ARCHIVED,
    ScoreFile,
    SessionScoreData,
    SessionScoreFileService,
    score_file_name,
)
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role
from tests.modules.reports.test_report_generation import _nilai, _ref, _rekaman

PENGUNDUH = uuid.uuid4()
RUN = uuid.uuid4()
DITERIMA = datetime(2026, 11, 2, tzinfo=UTC)


def _data(rekaman=None, **ubah) -> SessionScoreData:
    rekaman = rekaman or _rekaman("Produk A", [_nilai(1, "0.80", "0.60")])
    versi_kasus = {g.ref.case_version_id: nomor for nomor, g in enumerate(rekaman.graded_cases, 2)}
    bawaan = {
        "record": rekaman,
        "archived": False,
        "grading_run_id": RUN,
        "rubric_version_no": 3,
        "case_version_nos": versi_kasus,
    }
    return SessionScoreData(**{**bawaan, **ubah})


def _dengan(rekaman, **fakta):
    return replace(rekaman, facts=replace(rekaman.facts, **fakta))


class SumberTiruan:
    def __init__(self, *data: SessionScoreData) -> None:
        self._by_id = {d.record.facts.session_id: d for d in data}

    def read(self, session_id):
        return self._by_id.get(session_id)


class SimpananTiruan:
    """Hanya menambah; berkas yang isinya sama untuk sesi yang sama tidak disimpan dua kali."""

    def __init__(self) -> None:
        self.berkas: list[tuple[ScoreFile, uuid.UUID | None, uuid.UUID]] = []

    def keep(self, file, *, grading_run_id, created_by):
        for tersimpan, _, _ in self.berkas:
            if (tersimpan.session_id, tersimpan.sha256) == (file.session_id, file.sha256):
                return tersimpan
        self.berkas.append((file, grading_run_id, created_by))
        return file


def _unduh(*data, role=Role.ADMIN, sesi=None):
    simpanan = SimpananTiruan()
    layanan = SessionScoreFileService(SumberTiruan(*data), simpanan)
    target = sesi or data[0].record.facts.session_id
    return layanan.download(target, user_id=PENGUNDUH, role=role), simpanan


def _baris(berkas: ScoreFile) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(berkas.content.decode("utf-8-sig"))))


def _tolak(data, role=Role.ADMIN) -> tuple[ConflictError, SimpananTiruan]:
    simpanan = SimpananTiruan()
    layanan = SessionScoreFileService(SumberTiruan(data), simpanan)
    with pytest.raises(ConflictError) as galat:
        layanan.download(data.record.facts.session_id, user_id=PENGUNDUH, role=role)
    return galat.value, simpanan


# AC5: sesi layak menghasilkan satu berkas CSV untuk sesinya.


def test_sesi_layak_menghasilkan_satu_berkas_csv_bernama_sesinya():
    data = _data()

    berkas, _ = _unduh(data)

    sesi_id = data.record.facts.session_id
    assert berkas.session_id == sesi_id
    assert berkas.file_name == f"skor-{sesi_id}.csv"
    assert [b["Skor Deterministik"] for b in _baris(berkas)] == ["0.80"]


def test_nama_berkas_mengikuti_id_sesi():
    sesi_id = uuid.UUID("00000000-0000-0000-0000-000000000327")

    assert score_file_name(sesi_id) == "skor-00000000-0000-0000-0000-000000000327.csv"


def test_berkas_memuat_versi_kasus_versi_rubrik_dan_model():
    rekaman = _rekaman("Produk A", [_nilai(1, "0.5", "0.5")], model="model-9", judge_versi="j-4")

    berkas, _ = _unduh(_data(rekaman, rubric_version_no=7))

    baris = _baris(berkas)[0]
    assert baris["Versi Kasus"] == "2"
    assert baris["Versi Rubrik"] == "7"
    assert baris["Versi Model Produk"] == "model-9"
    assert baris["Model Penilai"] == "j-4"


def test_kasus_diurutkan_menurut_kodenya():
    rekaman = _rekaman("Produk A", [_nilai(3, "0.1", "0.1"), _nilai(1, "0.2", "0.2")])

    berkas, _ = _unduh(_data(rekaman))

    kode = [b["Kasus"] for b in _baris(berkas)]
    assert kode == sorted(kode)


def test_sesi_manual_ditandai_manual_dan_uji_coba_ditandai():
    rekaman = _rekaman(
        "Produk M",
        [_nilai(1, "0.5", "0.5")],
        session_type=SessionType.MANUAL,
        status=SessionStatus.FINALIZED,
        purpose=SessionPurpose.TRIAL,
    )

    berkas, _ = _unduh(_data(rekaman))

    baris = _baris(berkas)[0]
    assert baris["Jenis Pengukuran"] == "manual"
    assert baris["Uji Coba"] == "ya"


# AC6: hanya sesi yang selesai sepenuhnya dan sudah dinilai yang bisa diunduh.


@pytest.mark.parametrize(
    ("status", "kode"),
    [
        (SessionStatus.QUEUED, SESSION_NOT_FINISHED),
        (SessionStatus.RUNNING, SESSION_NOT_FINISHED),
        (SessionStatus.PAUSED, SESSION_NOT_FINISHED),
        (SessionStatus.CANCELLED, SESSION_CANCELLED),
        (SessionStatus.FAILED, SESSION_RUN_FAILED),
        (SessionStatus.FINISHED_WITH_FAILURES, SESSION_NOT_ACCEPTED_AS_IS),
    ],
)
def test_sesi_otomatis_yang_belum_selesai_sepenuhnya_ditolak_dengan_alasan(status, kode):
    rekaman = _rekaman("Produk A", [_nilai(1, "0.5", "0.5")], status=status)

    galat, simpanan = _tolak(_data(rekaman))

    assert galat.code == kode
    assert [alasan["code"] for alasan in galat.details["reasons"]] == [kode]
    assert simpanan.berkas == []


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.NOT_FILLED,
        SessionStatus.PARTIALLY_FILLED,
        SessionStatus.COMPLETE,
        SessionStatus.GRADED,
    ],
)
def test_sesi_manual_yang_belum_ditandai_selesai_ditolak(status):
    rekaman = _rekaman(
        "Produk M", [_nilai(1, "0.5", "0.5")], session_type=SessionType.MANUAL, status=status
    )

    galat, _ = _tolak(_data(rekaman))

    assert galat.code == SESSION_NOT_FINISHED


def test_sesi_selesai_tanpa_penilaian_ditolak_karena_belum_dinilai():
    rekaman = _rekaman("Produk A", [], rubrik=None)

    galat, _ = _tolak(_data(rekaman))

    assert galat.code == SESSION_NOT_GRADED


def test_semua_alasan_dikembalikan_bila_lebih_dari_satu():
    rekaman = _rekaman("Produk A", [], status=SessionStatus.FINISHED_WITH_FAILURES, rubrik=None)

    galat, _ = _tolak(_data(rekaman))

    assert [alasan["code"] for alasan in galat.details["reasons"]] == [
        SESSION_NOT_ACCEPTED_AS_IS,
        SESSION_NOT_GRADED,
    ]


def test_setiap_alasan_menjelaskan_kenapa_berkas_belum_bisa_diunduh():
    rekaman = _rekaman("Produk A", [], status=SessionStatus.CANCELLED, rubrik=None)

    galat, _ = _tolak(_data(rekaman))

    assert "diunduh" in galat.message
    assert all("diunduh" in alasan["message"] for alasan in galat.details["reasons"])


def test_sesi_otomatis_yang_diterima_apa_adanya_boleh_diunduh():
    rekaman = _dengan(
        _rekaman(
            "Produk A", [_nilai(1, "0.5", "0.5")], status=SessionStatus.FINISHED_WITH_FAILURES
        ),
        accepted_as_is_at=DITERIMA,
    )

    berkas, _ = _unduh(_data(rekaman))

    assert len(_baris(berkas)) == 1


def test_sesi_manual_yang_selesai_dan_dinilai_boleh_diunduh():
    rekaman = _rekaman(
        "Produk M",
        [_nilai(1, "0.5", "0.5")],
        session_type=SessionType.MANUAL,
        status=SessionStatus.FINALIZED,
    )

    berkas, _ = _unduh(_data(rekaman))

    assert len(_baris(berkas)) == 1


def test_sesi_arsip_ditolak_walaupun_selesai_dan_dinilai():
    # D2 kotak A: sesi arsip tidak layak untuk laporan, tren, maupun unduhan baru.
    galat, simpanan = _tolak(_data(archived=True))

    assert galat.code == SESSION_ARCHIVED
    assert simpanan.berkas == []


def test_sesi_yang_tidak_ada_menghasilkan_404():
    layanan = SessionScoreFileService(SumberTiruan(), SimpananTiruan())

    with pytest.raises(NotFoundError):
        layanan.download(uuid.uuid4(), user_id=PENGUNDUH, role=Role.ADMIN)


# AC7: berkas yang diserahkan disimpan.


def test_berkas_yang_diunduh_disimpan_bersama_run_penilaian_dan_pengunduhnya():
    berkas, simpanan = _unduh(_data())

    assert simpanan.berkas == [(berkas, RUN, PENGUNDUH)]


def test_unduhan_ulang_dengan_isi_sama_tidak_menambah_berkas_baru():
    data = _data()
    simpanan = SimpananTiruan()
    layanan = SessionScoreFileService(SumberTiruan(data), simpanan)

    pertama = layanan.download(data.record.facts.session_id, user_id=PENGUNDUH, role=Role.ADMIN)
    kedua = layanan.download(data.record.facts.session_id, user_id=PENGUNDUH, role=Role.ADMIN)

    assert kedua == pertama
    assert len(simpanan.berkas) == 1


def test_sidik_isi_berkas_adalah_sha256_isinya():
    import hashlib

    berkas, _ = _unduh(_data())

    assert berkas.sha256 == hashlib.sha256(berkas.content).hexdigest()


# Isi per peran: aturan D9 yang sudah berlaku, tertutup bawaan sampai SA memutuskan.


@pytest.fixture
def dev_dan_test():
    return _data(
        _rekaman(
            "Produk A",
            [
                _nilai(1, "0.5", "0.5", split=SplitTag.TEST),
                _nilai(2, "0.4", "0.4", split=SplitTag.DEV),
            ],
        )
    )


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
def test_viewer_dan_kontributor_hanya_mendapat_kasus_test(dev_dan_test, role):
    berkas, _ = _unduh(dev_dan_test, role=role)

    kode_test = dev_dan_test.record.graded_cases[0].ref.case_code
    assert [b["Kasus"] for b in _baris(berkas)] == [kode_test]


def test_admin_mendapat_kasus_dev_dan_test(dev_dan_test):
    berkas, _ = _unduh(dev_dan_test, role=Role.ADMIN)

    assert len(_baris(berkas)) == 2


def test_developer_ai_mendapat_kasus_dev_dengan_kode_test_disamarkan(dev_dan_test):
    berkas, _ = _unduh(dev_dan_test, role=Role.DEVELOPER_AI)

    kode = {b["Kasus"] for b in _baris(berkas)}
    kode_dev = dev_dan_test.record.graded_cases[1].ref.case_code
    assert kode == {MASKED_CASE_CODE, kode_dev}


# Review PR #152 B1: semua kasus sesi tertulis, dan berkas tanpa kasus tidak diserahkan.


def test_kasus_tanpa_skor_tetap_tertulis_sebagai_tidak_dinilai():
    gagal = _ref(2)
    rekaman = _dengan(
        _rekaman(
            "Produk A",
            [_nilai(1, "0.5", "0.5")],
            status=SessionStatus.FINISHED_WITH_FAILURES,
            snapshot_tambahan=(gagal,),
        ),
        accepted_as_is_at=DITERIMA,
    )
    versi = {rekaman.graded_cases[0].ref.case_version_id: 2, gagal.case_version_id: 5}

    berkas, _ = _unduh(_data(rekaman, case_version_nos=versi))

    baris = {b["Kasus"]: b for b in _baris(berkas)}
    assert baris[gagal.case_code]["Status Penilaian"] == "tidak dinilai"
    assert baris[gagal.case_code]["Skor Deterministik"] == ""
    assert baris[gagal.case_code]["Skor LLM"] == ""
    assert baris[gagal.case_code]["Versi Kasus"] == "5"
    assert baris[gagal.case_code]["Versi Rubrik"] == "3"
    assert baris[rekaman.graded_cases[0].ref.case_code]["Status Penilaian"] == "dinilai"


def test_kasus_dev_tanpa_skor_juga_tidak_terlihat_oleh_viewer():
    rekaman = _rekaman(
        "Produk A", [_nilai(1, "0.5", "0.5")], snapshot_tambahan=(_ref(2, split=SplitTag.DEV),)
    )

    berkas, _ = _unduh(_data(rekaman), role=Role.VIEWER)

    assert [b["Kasus"] for b in _baris(berkas)] == [rekaman.graded_cases[0].ref.case_code]


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
def test_sesi_yang_hanya_berisi_kasus_dev_ditolak_untuk_peran_tanpa_akses_dev(role):
    rekaman = _rekaman("Produk A", [_nilai(1, "0.5", "0.5", split=SplitTag.DEV)])

    galat, simpanan = _tolak(_data(rekaman), role=role)

    assert galat.code == NO_VISIBLE_CASES
    assert [alasan["code"] for alasan in galat.details["reasons"]] == [NO_VISIBLE_CASES]
    assert "diunduh" in galat.message
    assert simpanan.berkas == []


def test_sesi_yang_hanya_berisi_kasus_dev_tetap_bisa_diunduh_admin():
    rekaman = _rekaman("Produk A", [_nilai(1, "0.5", "0.5", split=SplitTag.DEV)])

    berkas, _ = _unduh(_data(rekaman), role=Role.ADMIN)

    assert len(_baris(berkas)) == 1


# Review PR #152 B2: D2 tetap mensyaratkan penilaian walaupun sesi diterima apa adanya.


def test_sesi_diterima_apa_adanya_tanpa_penilaian_ditolak_karena_belum_dinilai():
    rekaman = _dengan(
        _rekaman(
            "Produk A",
            [],
            status=SessionStatus.FINISHED_WITH_FAILURES,
            rubrik=None,
            snapshot_tambahan=(_ref(1),),
        ),
        accepted_as_is_at=DITERIMA,
    )

    galat, simpanan = _tolak(_data(rekaman))

    assert galat.code == SESSION_NOT_GRADED
    assert [alasan["code"] for alasan in galat.details["reasons"]] == [SESSION_NOT_GRADED]
    assert simpanan.berkas == []
