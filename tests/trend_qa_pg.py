"""Bantuan bersama QA tren dan penerbitan laporan. PBI-17 (SCRUM-84), SCRUM-336.

Semua data dibuat lewat HTTP seperti pengguna (L3, sintetis): sesi otomatis dijalankan worker
dengan provider palsu, sesi manual diisi Kontributor, keduanya dinilai penilai uji, laporan
dibuat lewat POST /reports dan diterbitkan lewat POST /reports/{id}/publish. Satu-satunya
tulisan langsung ke database adalah memundurkan waktu sesi: tren dihitung per bulan WIB dari
waktu sesi selesai (D4, #153), dan tes tidak bisa menunggu berbulan-bulan.

Berbeda dengan dunia QA PBI-16 (tests/drilldown_qa_pg.py di #154), dunia ini tidak membuat
sesi di depan: setiap tes membangun sendiri sesi yang ia butuhkan per bulan, karena tren
butuh deret sesi yang berbeda per tes. Fondasinya sama (database salinan per tes, helper
kasus dan worker yang sudah ada); setelah keduanya masuk, keduanya bisa disatukan.

Hanya PostgreSQL, sama dengan lingkungan Docker. Tiap tes memakai database salinan sendiri,
bukan satu transaksi yang di-rollback: di dalam satu transaksi now() berhenti, jadi laporan
tidak akan menemukan run penilaian yang selesai sebelumnya.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.orm import Session

from app.main import app
from app.modules.auth.models import User
from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.reports.wib import WIB
from app.modules.runs import dependencies, exchange_rate, session_grade
from app.shared import request_context
from app.shared.config import get_settings
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, get_current_user
from tests.grading_pg import aktifkan, buat_versi_aktif
from tests.judge_rows import tambah_penilai
from tests.modules.cases.test_cases import _buat, _setujui, _suite
from tests.modules.runs.test_execution_runtime import runner
from tests.modules.runs.test_manual_grade_http_qa import PenilaiUji
from tests.modules.runs.test_summary_endpoint import _settings, _tambah_produk

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
HANYA_POSTGRES = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_POSTGRES_URL, reason="TEST_POSTGRES_URL not set"),
]
DIPEROLEH = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)
# Skor deterministik 1.00 hanya bila jawaban menyebut pasal dan peraturannya (rujukan kasus uji).
PASAL = "Pasal 151 UU 13/2003"


@dataclass(frozen=True)
class Orang:
    nama: str
    peran: Role

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"qa-pbi17/{self.nama}")

    @property
    def email(self) -> str:
        return f"{self.nama.lower().replace(' ', '.')}@veritask.test"


ADMIN = Orang("Admin Tren", Role.ADMIN)
KONTRIBUTOR = Orang("Kontributor Tren", Role.CONTRIBUTOR)
VIEWER = Orang("Viewer Tren", Role.VIEWER)
DEV_AI = Orang("Dev AI Tren", Role.DEVELOPER_AI)
SEMUA = (ADMIN, KONTRIBUTOR, VIEWER, DEV_AI)


@dataclass(frozen=True)
class Kasus:
    kode: str
    split: str
    pertanyaan: str
    wajib: str
    kesimpulan: str


KASUS = (
    Kasus("TRN-001", "test", "Apakah PHK tanpa surat sah?", "surat", "tidak sah"),
    Kasus("TRN-002", "test", "Berapa lama masa percobaan kerja?", "tiga bulan", "paling lama"),
    Kasus("TRN-003", "dev", "Apakah pesangon wajib dibayar?", "pesangon", "wajib dibayar"),
    Kasus("TRN-004", "dev", "Bolehkah upah di bawah UMP?", "UMP", "dilarang"),
)
KODE_TEST = ("TRN-001", "TRN-002")


def jawaban(kasus: Kasus, mutu: str) -> str:
    """Jawaban sintetis dengan mutu yang bisa diatur, supaya tiap bulan punya skor berbeda.

    baik: rujukan, unsur wajib, dan kesimpulan (deterministik dan LLM penuh). sedang: unsur
    wajib saja (LLM separuh, deterministik nol). buruk: tidak ada yang terpenuhi.
    """
    if mutu == "baik":
        return f"Soal {kasus.wajib}: {kasus.kesimpulan} menurut {PASAL}."
    if mutu == "sedang":
        return f"Soal {kasus.wajib}, tergantung keadaan."
    return "Belum bisa dijawab."


class PenilaiTren(PenilaiUji):
    """Penilai uji dengan versi model, supaya syarat model penilai sama (B2) terpenuhi."""

    model_version = "judge-qa-1"


class PenilaiTrenBaru(PenilaiTren):
    """Model penilai lain: sesi yang dinilainya memutus garis tren (AC5, AC13)."""

    model_version = "judge-qa-2"


class ProviderTren:
    """Provider palsu: jawaban per pertanyaan dengan mutu yang dipilih, tanpa gagal teknis
    kecuali kode di `gagal` (setiap panggilan kasus itu gagal, jadi kasusnya tanpa skor)."""

    def __init__(self, mutu: str = "baik", *, model: str = "aiyu-2026-11", gagal=()) -> None:
        self._jawaban = {k.pertanyaan: jawaban(k, mutu) for k in KASUS}
        self._gagal = {k.pertanyaan for k in KASUS if k.kode in set(gagal)}
        self._model = model

    def ask(self, prompt, **_):
        gagal = prompt in self._gagal
        return ProviderResponse(
            text="" if gagal else self._jawaban[prompt],
            model_version=self._model,
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
    penilai_id: uuid.UUID
    monkeypatch: pytest.MonkeyPatch
    rubrik_id: uuid.UUID
    produk: dict[str, AiProduct] = field(default_factory=dict)

    def sebagai(self, orang: Orang) -> TestClient:
        """Ganti pengguna untuk request BERIKUTNYA. Override ini global dan client-nya sama,
        jadi client yang disimpan di variabel ikut berganti pengguna saat sebagai() dipanggil
        lagi; selalu panggil sebagai() tepat sebelum request."""
        pengguna = CurrentUser(
            user_id=orang.id, name=orang.nama, email=orang.email, role=orang.peran
        )

        def masuk(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
            # Sama dengan get_current_user asli: pelaku diikat ke session untuk audit (PBI-18).
            request_context.bind(
                db,
                user_id=pengguna.user_id,
                role=pengguna.role,
                request_id=getattr(request.state, "request_id", None),
            )
            return pengguna

        app.dependency_overrides[get_current_user] = masuk
        return self.client

    def pakai_penilai(self, penilai: type[PenilaiTren]) -> None:
        self.monkeypatch.setattr(
            session_grade,
            "resolve_active_judge_asker",
            lambda db, session, judge_asker, judge_id=None: (judge_asker or penilai(), None, None),
        )


@contextmanager
def _database_baru(server: URL, templat: str | None = None) -> Iterator[URL]:
    nama = f"qa_pbi17_{uuid.uuid4().hex[:12]}"
    salin = f' TEMPLATE "{templat}"' if templat else ""
    admin = create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nama}"{salin}'))
        yield server.set(database=nama)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nama}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def templat_tren() -> Iterator[URL]:
    """Database yang dimigrasi sekali per sesi pytest, sumber salinan setiap tes.

    Bukan database TEST_POSTGRES_URL itu sendiri: lifespan aplikasi menahan koneksi ke
    database bawaan, dan PostgreSQL menolak menyalin database yang sedang dipakai.
    """
    with _database_baru(make_url(TEST_POSTGRES_URL)) as url:
        env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
        root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env, check=True
        )
        yield url


@pytest.fixture
def db_tren(templat_tren: URL) -> Iterator[Session]:
    with _database_baru(templat_tren, templat=templat_tren.database) as url:
        engine = create_engine(url)
        with Session(engine) as db:
            yield db
        engine.dispose()


@pytest.fixture
def dunia_tren(db_tren: Session, monkeypatch) -> Iterator[Dunia]:
    """Pengguna empat peran, rubrik v1 aktif, penilai, satu suite empat kasus (2 test, 2 dev),
    produk otomatis AiYU dan Justisio, dan produk manual LegalHero. Belum ada sesi."""
    db = db_tren
    db.add_all(User(id=o.id, email=o.email, name=o.nama, role=o.peran) for o in SEMUA)
    db.commit()
    # Trigger: versi rubrik baru mulai nonaktif dan diaktifkan lewat UPDATE.
    rubrik_id = buat_versi_aktif(db.connection(), ADMIN.id)
    db.commit()

    semula = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[dependencies.get_exchange_rate] = lambda: (
        exchange_rate.ConfiguredExchangeRate(Decimal("16000"))
    )
    # finally: setup yang gagal tidak boleh meninggalkan get_db ke database yang sudah dihapus.
    try:
        with TestClient(app) as client:
            d = Dunia(
                client=client,
                db=db,
                suite_id="",
                penilai_id=tambah_penilai(db, ADMIN.id).id,
                monkeypatch=monkeypatch,
                rubrik_id=rubrik_id,
            )
            d.suite_id = buat_suite(d)
            for nama in ("AiYU", "Justisio"):
                d.produk[nama] = _tambah_produk(db, name=nama, created_by=ADMIN.id)
            d.produk["LegalHero"] = _produk_manual(db, "LegalHero")
            d.pakai_penilai(PenilaiTren)
            yield d
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(semula)


def _produk_manual(db: Session, nama: str) -> AiProduct:
    produk = AiProduct(
        name=nama,
        measurement_type=MeasurementType.MANUAL,
        vendor_name=nama,
        site_url="https://contoh.test",
        manual_params="Paket uji",
        created_by=ADMIN.id,
    )
    db.add(produk)
    db.commit()
    return produk


def buat_suite(d: Dunia, nama: str = "Tren ketenagakerjaan", akhiran: str = "") -> str:
    """Suite baru berisi keempat kasus, semuanya disetujui. Kode kasus suite kedua diberi
    akhiran, supaya dua suite tidak berbagi kode."""
    suite_id = _suite(d.sebagai(KONTRIBUTOR), nama)
    for kasus in KASUS:
        respons = _buat(
            d.sebagai(KONTRIBUTOR),
            suite_id,
            case_code=f"{kasus.kode}{akhiran}",
            split_tag=kasus.split,
            identity={
                "title": kasus.kode,
                "question": kasus.pertanyaan,
                "category": "ketenagakerjaan",
            },
            answer_criteria={
                "must_contain": [kasus.wajib],
                "must_not_contain": [],
                "expected_conclusion": kasus.kesimpulan,
            },
        )
        assert respons.status_code == 201, respons.text
        _setujui(d.db, respons.json()["id"])
    return suite_id


# --- Waktu -------------------------------------------------------------------------------


def awal_bulan(mundur: int) -> date:
    """Hari pertama bulan kalender `mundur` bulan sebelum bulan ini, dalam WIB seperti server."""
    hari_ini = datetime.now(UTC).astimezone(WIB).date()
    indeks = hari_ini.year * 12 + hari_ini.month - 1 - mundur
    return date(indeks // 12, indeks % 12 + 1, 1)


def waktu_di_bulan(mundur: int, hari: int = 10, jam: int = 12) -> datetime:
    """Pukul `jam` WIB pada tanggal `hari` di bulan itu; bawaannya jauh dari batas hari."""
    awal = awal_bulan(mundur)
    return datetime(awal.year, awal.month, hari, jam, 0, tzinfo=WIB)


def mundurkan(d: Dunia, sesi_id: uuid.UUID, kapan: datetime) -> None:
    """Satu-satunya tulisan langsung: semua waktu sesi dipindah ke `kapan`.

    Tren memakai waktu selesai (finished_at otomatis, finalized_at manual); waktu dibuat dan
    mulai ikut dipindah supaya urutannya tetap masuk akal. Kolom yang kosong tetap kosong.
    """
    d.db.execute(
        text(
            "UPDATE measurement_sessions SET created_at = :kapan, "
            "started_at = CASE WHEN started_at IS NULL THEN NULL ELSE :kapan END, "
            "finished_at = CASE WHEN finished_at IS NULL THEN NULL ELSE :kapan END, "
            "finalized_at = CASE WHEN finalized_at IS NULL THEN NULL ELSE :kapan END "
            "WHERE id = :id"
        ),
        {"kapan": kapan, "id": sesi_id},
    )
    d.db.commit()


# --- Sesi --------------------------------------------------------------------------------


def _badan_sesi(d: Dunia, produk: AiProduct, **ubah) -> dict:
    return {
        "suite_ids": [d.suite_id],
        "product_id": str(produk.id),
        "repeat_count": 1,
        "case_filter": "both",
        "label": f"Tren {produk.name}",
        "purpose": "official",
        "judge_provider_id": str(d.penilai_id),
        **ubah,
    }


def nilai(d: Dunia, sesi_id: uuid.UUID) -> dict:
    respons = d.sebagai(ADMIN).post(f"/measurements/{sesi_id}/grade", json={})
    assert respons.status_code == 200, respons.text
    return respons.json()


def sesi_otomatis(
    d: Dunia,
    nama: str,
    *,
    kapan: datetime,
    provider: ProviderTren | None = None,
    label: str | None = None,
    **ubah,
) -> uuid.UUID:
    """Sesi otomatis yang selesai dan dinilai (layak tren), dibuat pada waktu `kapan`.

    Sesi yang selesai dengan kegagalan diterima apa adanya lebih dulu, supaya tetap layak.
    """
    produk = d.produk[nama]
    badan = _badan_sesi(d, produk, label=label or f"{nama} {kapan:%Y-%m-%d}", **ubah)
    respons = d.sebagai(ADMIN).post("/measurements", json=badan)
    assert respons.status_code == 201, respons.text
    sesi_id = uuid.UUID(respons.json()["session_id"])
    siap = SimpleNamespace(produk=produk, db=d.db)
    assert runner(siap, provider or ProviderTren())() is True
    d.db.expire_all()
    status = d.sebagai(ADMIN).get(f"/measurements/{sesi_id}").json()["status"]
    if status == "finished_with_failures":
        terima = d.sebagai(ADMIN).patch(
            f"/measurements/{sesi_id}/accept", json={"reason": "Gangguan penyedia, hasil cukup"}
        )
        assert terima.status_code == 200, terima.text
    nilai(d, sesi_id)
    mundurkan(d, sesi_id, kapan)
    return sesi_id


def sesi_manual(
    d: Dunia,
    nama: str,
    *,
    kapan: datetime,
    mutu: str = "baik",
    pengulangan: int = 1,
    label: str | None = None,
) -> uuid.UUID:
    """Sesi manual yang diisi Kontributor, dinilai, lalu ditandai Selesai (layak tren)."""
    produk = d.produk[nama]
    badan = _badan_sesi(
        d, produk, repeat_count=pengulangan, label=label or f"{nama} {kapan:%Y-%m-%d}"
    )
    respons = d.sebagai(ADMIN).post("/manual-measurements", json=badan)
    assert respons.status_code == 201, respons.text
    sesi_id = respons.json()["session_id"]
    detail = d.sebagai(ADMIN).get(f"/manual-measurements/{sesi_id}").json()
    teks = {k.kode: jawaban(k, mutu) for k in KASUS}
    for kasus in detail["cases"]:
        for ke in range(1, pengulangan + 1):
            badan_jawaban = {
                "snapshot_item_id": kasus["snapshot_item_id"],
                "attempt_no": ke,
                "answer_md": teks[kasus["case_code"]],
                "obtained_at": DIPEROLEH.isoformat(),
                "platform_refused": False,
                "confirm": False,
            }
            simpan = d.sebagai(KONTRIBUTOR).put(
                f"/manual-measurements/{sesi_id}/answers", json=badan_jawaban
            )
            assert simpan.status_code == 200, simpan.text
    nilai(d, uuid.UUID(sesi_id))
    selesai = d.sebagai(ADMIN).post(f"/manual-measurements/{sesi_id}/finalize")
    assert selesai.status_code == 200, selesai.text
    mundurkan(d, uuid.UUID(sesi_id), kapan)
    return uuid.UUID(sesi_id)


def rubrik_baru(d: Dunia, versi: int = 2) -> None:
    """Ganti rubrik aktif: sesi yang dinilai sesudahnya memakai versi rubrik lain (AC5)."""
    aktifkan(d.db.connection(), d.rubrik_id, aktif=False)
    d.rubrik_id = buat_versi_aktif(d.db.connection(), ADMIN.id, version_no=versi)
    d.db.commit()


def versi_kasus_baru(d: Dunia, kode: str) -> None:
    """Kasus `kode` mendapat versi baru yang disetujui; snapshot sesi berikutnya memakainya."""
    kasus = d.sebagai(ADMIN).get(f"/suites/{d.suite_id}/cases").json()
    (case_id,) = [k["id"] for k in kasus if k["case_code"] == kode]
    respons = d.sebagai(KONTRIBUTOR).post(f"/cases/{case_id}/versions")
    assert respons.status_code == 201, respons.text
    _setujui(d.db, case_id)


# --- Tren dan laporan --------------------------------------------------------------------


def tren(d: Dunia, nama: str, oleh: Orang = ADMIN, *, pdf: bool = False, **parameter):
    """GET /reports/trend (atau /pdf) untuk produk `nama`; parameter lain diteruskan apa adanya."""
    jalur = "/reports/trend/pdf" if pdf else "/reports/trend"
    params = {"product_id": str(d.produk[nama].id), **parameter}
    return d.sebagai(oleh).get(jalur, params=params)


def titik(respons) -> list[dict]:
    assert respons.status_code == 200, respons.text
    return respons.json()["points"]


def bulan_titik(respons) -> list[str]:
    return [p["month"] for p in titik(respons)]


def buat_laporan(
    d: Dunia,
    sesi: list[uuid.UUID],
    *,
    oleh: Orang = ADMIN,
    scope: str = "test",
    label: str = "real_name",
    judul: str | None = None,
) -> dict:
    """POST /reports dari sesi yang diberikan, lalu detailnya dibaca pembuatnya."""
    badan = {
        "title": judul or f"QA PBI-17 {scope}",
        "session_ids": [str(s) for s in sesi],
        "score_scope": scope,
        "product_label_mode": label,
    }
    respons = d.sebagai(oleh).post("/reports", json=badan)
    assert respons.status_code == 201, respons.text
    detail = d.sebagai(oleh).get(f"/reports/{respons.json()['report']['id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def angka(laporan: dict, produk: str, split: str) -> dict:
    (sesi,) = [s for s in laporan["sessions"] if s["product_label"] == produk]
    (hasil,) = [r for r in sesi["results"] if r["score_split"] == split]
    return hasil
