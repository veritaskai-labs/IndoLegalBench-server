"""Bantuan bersama QA penelusuran angka laporan dan unduh skor. PBI-16 (SCRUM-83), SCRUM-330.

Semua data dibuat lewat HTTP seperti pengguna (L3, sintetis): sesi otomatis dijalankan worker
dengan provider palsu, sesi manual diisi Kontributor, keduanya dinilai penilai uji, lalu
laporan dibuat lewat POST /reports. Tidak ada baris laporan yang disisipkan langsung, jadi
angka laporan, penelusuran, dan berkas skor terbukti berasal dari data yang sama.
"""

import csv
import io
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.modules.auth.models import User
from app.modules.grading.models import GradingRun, RubricVersion
from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.runs import dependencies, exchange_rate, session_grade
from app.shared.config import get_settings
from app.shared.security import CurrentUser, Role
from tests.judge_rows import tambah_penilai
from tests.modules.cases.test_cases import _buat, _ganti, _setujui, _suite
from tests.modules.runs.test_execution_runtime import runner
from tests.modules.runs.test_manual_evidence_http_qa import PNG
from tests.modules.runs.test_manual_grade_http_qa import PenilaiUji
from tests.modules.runs.test_manual_session_endpoint import _produk_manual
from tests.modules.runs.test_summary_endpoint import _settings, _tambah_produk

DIPEROLEH = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)
# Penanda di setiap jawaban asli: tidak boleh muncul di respons yang tidak berhak isi kasus.
RAHASIA = "Rahasia-QA-330"
# Skor deterministik 1.00 hanya bila jawaban menyebut pasal dan peraturannya.
PASAL = "Pasal 151 UU 13/2003"


@dataclass(frozen=True)
class Orang:
    nama: str
    peran: Role

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"qa-pbi16/{self.nama}")

    @property
    def email(self) -> str:
        return f"{self.nama.lower().replace(' ', '.')}@veritask.test"


ADMIN = Orang("Admin QA", Role.ADMIN)
KONTRIBUTOR = Orang("Kontributor QA", Role.CONTRIBUTOR)
VIEWER = Orang("Viewer QA", Role.VIEWER)
DEV_AI = Orang("Dev AI QA", Role.DEVELOPER_AI)
SEMUA = (ADMIN, KONTRIBUTOR, VIEWER, DEV_AI)


@dataclass(frozen=True)
class Kasus:
    kode: str
    split: str
    pertanyaan: str
    wajib: str
    kesimpulan: str
    jawaban_otomatis: str
    jawaban_manual: str


# Jawaban berbeda per kasus dan per produk, supaya rata-rata tiap angka laporan bermakna.
KASUS = (
    Kasus(
        "PHK-001",
        "test",
        "Apakah PHK tanpa surat sah?",
        "surat",
        "tidak sah",
        f"PHK tanpa surat tidak sah menurut {PASAL}. {RAHASIA}",
        f"Tanpa surat, PHK tidak sah. {RAHASIA}",
    ),
    Kasus(
        "PHK-002",
        "test",
        "Berapa lama masa percobaan kerja?",
        "tiga bulan",
        "paling lama tiga bulan",
        f"Masa percobaan biasanya sebentar. {RAHASIA}",
        f"Masa percobaan paling lama tiga bulan sesuai {PASAL}. {RAHASIA}",
    ),
    Kasus(
        "PHK-003",
        "dev",
        "Apakah pesangon wajib dibayar saat PHK?",
        "pesangon",
        "wajib",
        f"Pesangon wajib dibayar menurut {PASAL}. {RAHASIA}",
        f"Tergantung perjanjian kerja. {RAHASIA}",
    ),
    Kasus(
        "PHK-004",
        "dev",
        "Bolehkah upah di bawah UMP?",
        "UMP",
        "tidak boleh",
        f"Upah di bawah UMP tidak boleh. {RAHASIA}",
        f"Upah di bawah UMP tidak boleh menurut {PASAL}. {RAHASIA}",
    ),
)
KODE_TEST = ("PHK-001", "PHK-002")
KODE_DEV = ("PHK-003", "PHK-004")
KASUS_GAGAL_SEKALI = "PHK-002"
BUKTI_DI = "PHK-001"


class PenilaiQA(PenilaiUji):
    """Penilai uji dengan versi model, supaya syarat B2 (model penilai sama) terpenuhi."""

    model_version = "judge-qa-1"


class PenilaiPelit(PenilaiQA):
    """Penilai untuk nilai ulang: tidak ada kriteria yang terpenuhi, jadi skor LLM berubah."""

    @staticmethod
    def _ada(kriteria: str, jawaban: str) -> bool:
        return False


class ProviderQA:
    """Provider palsu: jawaban per kasus; panggilan pertama untuk kode di `gagal` gagal teknis.

    selalu_gagal membuat setiap panggilan kasus itu gagal, jadi kasusnya tidak punya skor.
    """

    def __init__(self, gagal: Iterable[str] = (KASUS_GAGAL_SEKALI,), selalu_gagal=()) -> None:
        self._jawaban = {k.pertanyaan: k.jawaban_otomatis for k in KASUS}
        self._gagal = {k.pertanyaan for k in KASUS if k.kode in set(gagal)}
        self._selalu = {k.pertanyaan for k in KASUS if k.kode in set(selalu_gagal)}
        self.on_call = None

    def ask(self, prompt, **_):
        if self.on_call:
            self.on_call()
        gagal = prompt in self._gagal or prompt in self._selalu
        self._gagal.discard(prompt)
        return ProviderResponse(
            text="" if gagal else self._jawaban[prompt],
            model_version="aiyu-2026-11",
            latency_ms=5,
            raw={"ok": not gagal},
            error="provider error" if gagal else None,
            failure_kind=FailureKind.PROVIDER_ERROR if gagal else None,
            input_tokens=10,
            output_tokens=20,
        )


@dataclass
class Dunia:
    client: TestClient
    db: Session
    suite_id: str
    otomatis: SimpleNamespace
    manual: SimpleNamespace
    penilai_id: uuid.UUID
    monkeypatch: pytest.MonkeyPatch

    def sebagai(self, orang: Orang) -> TestClient:
        _ganti(
            self.client,
            CurrentUser(user_id=orang.id, name=orang.nama, email=orang.email, role=orang.peran),
        )
        return self.client

    def pakai_penilai(self, penilai: type[PenilaiQA]) -> None:
        self.monkeypatch.setattr(
            session_grade,
            "resolve_active_judge_asker",
            lambda db, session, judge_asker, judge_id=None: (judge_asker or penilai(), None, None),
        )


@pytest.fixture
def dunia(as_role, db_session, monkeypatch) -> Dunia:
    """Sesi otomatis AiYU (satu percobaan gagal, diterima apa adanya) dan sesi manual
    LegalHero (bukti di PHK-001), keduanya sudah dinilai dan layak laporan."""
    client = as_role(Role.CONTRIBUTOR)
    db_session.add_all(User(id=o.id, email=o.email, name=o.nama, role=o.peran) for o in SEMUA)
    db_session.commit()
    suite_id = _suite(client)
    for kasus in KASUS:
        _setujui(db_session, _buat_kasus(client, suite_id, kasus))

    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[dependencies.get_exchange_rate] = lambda: (
        exchange_rate.ConfiguredExchangeRate(Decimal("16000"))
    )
    db_session.add(_rubrik_aktif())
    d = Dunia(
        client=client,
        db=db_session,
        suite_id=suite_id,
        otomatis=SimpleNamespace(produk=_tambah_produk(db_session), db=db_session),
        manual=SimpleNamespace(produk=_produk_manual(db_session), db=db_session),
        penilai_id=tambah_penilai(db_session, ADMIN.id).id,
        monkeypatch=monkeypatch,
    )
    d.pakai_penilai(PenilaiQA)
    d.otomatis.id = jalankan_sesi_otomatis(d)
    terima_apa_adanya(d, d.otomatis.id)
    nilai(d, d.otomatis.id)
    d.manual.id = sesi_manual_selesai(d)
    penilaian_semenit_lalu(d)
    return d


def penilaian_semenit_lalu(d: Dunia) -> None:
    """SQLite menyimpan now() laporan per detik, finished_at run per mikrodetik. Tanpa geser ini
    laporan yang dibuat di detik yang sama dengan penilaian tidak menemukan run bekunya."""
    for run in d.db.scalars(select(GradingRun)):
        run.triggered_at -= timedelta(minutes=1)
        run.finished_at = run.finished_at and run.finished_at - timedelta(minutes=1)
    d.db.commit()


def _buat_kasus(client: TestClient, suite_id: str, kasus: Kasus) -> str:
    respons = _buat(
        client,
        suite_id,
        case_code=kasus.kode,
        split_tag=kasus.split,
        identity={"title": kasus.kode, "question": kasus.pertanyaan, "category": "ketenagakerjaan"},
        answer_criteria={
            "must_contain": [kasus.wajib],
            "must_not_contain": [],
            "expected_conclusion": kasus.kesimpulan,
        },
    )
    assert respons.status_code == 201, respons.text
    return respons.json()["id"]


def _rubrik_aktif() -> RubricVersion:
    return RubricVersion(
        version_no=1,
        effective_date=date.today(),
        is_active=True,
        locked_at=datetime.now(UTC),
        judge_prompt_quality="Prompt kualitas",
        judge_prompt_format="Prompt format",
        created_by=ADMIN.id,
    )


def _badan_sesi(d: Dunia, produk_id: uuid.UUID, **ubah) -> dict:
    return {
        "suite_ids": [d.suite_id],
        "product_id": str(produk_id),
        "repeat_count": 2,
        "case_filter": "both",
        "label": "QA PBI-16",
        "purpose": "official",
        "judge_provider_id": str(d.penilai_id),
        **ubah,
    }


def mulai_sesi_otomatis(d: Dunia, **ubah) -> uuid.UUID:
    badan = _badan_sesi(d, d.otomatis.produk.id, **ubah)
    respons = d.sebagai(ADMIN).post("/measurements", json=badan)
    assert respons.status_code == 201, respons.text
    return uuid.UUID(respons.json()["session_id"])


def jalankan_worker(d: Dunia, provider: ProviderQA) -> None:
    assert runner(d.otomatis, provider)() is True
    d.db.expire_all()


def jalankan_sesi_otomatis(d: Dunia, provider: ProviderQA | None = None, **ubah) -> uuid.UUID:
    sesi_id = mulai_sesi_otomatis(d, **ubah)
    jalankan_worker(d, provider or ProviderQA())
    return sesi_id


def terima_apa_adanya(d: Dunia, sesi_id: uuid.UUID) -> None:
    respons = d.sebagai(ADMIN).patch(
        f"/measurements/{sesi_id}/accept", json={"reason": "Gangguan penyedia, hasil cukup"}
    )
    assert respons.status_code == 200, respons.text


def nilai(d: Dunia, sesi_id: uuid.UUID) -> dict:
    respons = d.sebagai(ADMIN).post(f"/measurements/{sesi_id}/grade", json={})
    assert respons.status_code == 200, respons.text
    return respons.json()


def buat_sesi_manual(d: Dunia, **ubah) -> str:
    badan = _badan_sesi(d, d.manual.produk.id, repeat_count=1, **ubah)
    respons = d.sebagai(ADMIN).post("/manual-measurements", json=badan)
    assert respons.status_code == 201, respons.text
    return respons.json()["session_id"]


def isi_jawaban_manual(d: Dunia, sesi_id: str, kode: Iterable[str] | None = None) -> None:
    """Kontributor mengisi kasus `kode` (bawaan semua); kasus BUKTI_DI ikut diberi bukti PNG."""
    jawaban = {k.kode: k.jawaban_manual for k in KASUS}
    pilih = set(kode) if kode is not None else set(jawaban)
    detail = d.sebagai(ADMIN).get(f"/manual-measurements/{sesi_id}").json()
    for kasus in detail["cases"]:
        if kasus["case_code"] not in pilih:
            continue
        badan = {
            "snapshot_item_id": kasus["snapshot_item_id"],
            "attempt_no": 1,
            "answer_md": jawaban[kasus["case_code"]],
            "obtained_at": DIPEROLEH.isoformat(),
            "platform_refused": False,
            "confirm": False,
        }
        simpan = d.sebagai(KONTRIBUTOR).put(f"/manual-measurements/{sesi_id}/answers", json=badan)
        assert simpan.status_code == 200, simpan.text
        if kasus["case_code"] == BUKTI_DI:
            jalur = f"/manual-measurements/{sesi_id}/answers/{kasus['snapshot_item_id']}/1/evidence"
            bukti = d.sebagai(KONTRIBUTOR).post(
                jalur, files={"file": ("chat.png", PNG, "image/png")}
            )
            assert bukti.status_code == 201, bukti.text


def tandai_selesai(d: Dunia, sesi_id: str) -> None:
    respons = d.sebagai(ADMIN).post(f"/manual-measurements/{sesi_id}/finalize")
    assert respons.status_code == 200, respons.text


def sesi_manual_selesai(d: Dunia, **ubah) -> uuid.UUID:
    sesi_id = buat_sesi_manual(d, **ubah)
    isi_jawaban_manual(d, sesi_id)
    nilai(d, uuid.UUID(sesi_id))
    tandai_selesai(d, sesi_id)
    return uuid.UUID(sesi_id)


def buat_laporan(
    d: Dunia, *, oleh: Orang = ADMIN, scope: str = "dev_test", label: str = "real_name"
) -> dict:
    """POST /reports dari sesi AiYU dan LegalHero, lalu detailnya dibaca pembuatnya."""
    badan = {
        "title": f"QA PBI-16 {scope}",
        "session_ids": [str(d.otomatis.id), str(d.manual.id)],
        "score_scope": scope,
        "product_label_mode": label,
    }
    respons = d.sebagai(oleh).post("/reports", json=badan)
    assert respons.status_code == 201, respons.text
    return baca_laporan(d, respons.json()["report"]["id"], oleh)


def baca_laporan(d: Dunia, report_id: str, oleh: Orang = ADMIN) -> dict:
    respons = d.sebagai(oleh).get(f"/reports/{report_id}")
    assert respons.status_code == 200, respons.text
    return respons.json()


def terbitkan(d: Dunia, laporan: dict) -> None:
    respons = d.sebagai(ADMIN).post(f"/reports/{laporan['id']}/publish", json={})
    assert respons.status_code == 201, respons.text


def angka(laporan: dict, produk: str, split: str) -> dict:
    """Satu angka laporan: hasil jenis skor `split` milik produk berlabel `produk`."""
    (sesi,) = [s for s in laporan["sessions"] if s["product_label"] == produk]
    (hasil,) = [r for r in sesi["results"] if r["score_split"] == split]
    return hasil


def jalur_kasus(laporan: dict, hasil: dict, case_id: str | None = None) -> str:
    jalur = f"/reports/{laporan['id']}/results/{hasil['id']}/cases"
    return f"{jalur}/{case_id}" if case_id else jalur


def daftar_kasus(d: Dunia, laporan: dict, hasil: dict, oleh: Orang = ADMIN) -> dict:
    respons = d.sebagai(oleh).get(jalur_kasus(laporan, hasil))
    assert respons.status_code == 200, respons.text
    return respons.json()


def id_kasus(d: Dunia, kode: str) -> str:
    """Id kasus dari daftar kasus suite (kode kasus disamarkan untuk Dev AI, id tidak)."""
    suite = d.sebagai(ADMIN).get(f"/suites/{d.suite_id}/cases")
    assert suite.status_code == 200, suite.text
    (kasus,) = [k for k in suite.json() if k["case_code"] == kode]
    return kasus["id"]


def detail_kasus(d: Dunia, laporan: dict, hasil: dict, kode: str, oleh: Orang = ADMIN):
    jalur = jalur_kasus(laporan, hasil, id_kasus(d, kode))  # id_kasus berganti ke Admin
    return d.sebagai(oleh).get(jalur)


def unduh_skor(d: Dunia, sesi_id, oleh: Orang = ADMIN):
    return d.sebagai(oleh).get(f"/reports/sessions/{sesi_id}/csv")


def baris_csv(isi: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(isi.decode("utf-8-sig"))))
