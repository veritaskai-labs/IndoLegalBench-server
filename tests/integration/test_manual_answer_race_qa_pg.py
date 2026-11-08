"""Dua orang menyimpan jawaban di slot yang sama bersamaan, di PostgreSQL. PBI-22 AC5, SCRUM-238.

AC5: beberapa orang boleh mengisi sesi yang sama, menimpa jawaban orang lain butuh
konfirmasi, dan versi sebelumnya tetap tersimpan. Catatan klien: tim legal 4 orang mengisi
bersamaan. Tes unit SCRUM-231 menguji timpa secara berurutan; di sini dua koneksi
bertabrakan di slot yang sama:

- dua jawaban pertama tanpa konfirmasi: yang kedua harus melihat jawaban pertama dan ditolak
  (409 CONFIRMATION_REQUIRED), bukan diam-diam menimpanya;
- dua timpa dengan konfirmasi: tidak ada versi yang hilang, riwayat berisi keduanya urut.

SQLite mengabaikan kunci baris, jadi tes ini harus di PostgreSQL. Tanpa jeda waktu: penyimpan
pertama ditahan setelah memegang kunci, dan penyimpan kedua baru dilepas setelah
pg_stat_activity menunjukkan ia benar-benar menunggu kunci. lock_timeout 5 detik menjaga tes
yang salah agar tidak menggantung.

Data harus di-commit supaya terlihat lintas koneksi. CI menjalankan semua tes PostgreSQL di
satu database, dan tes downgrade migrasi sesudahnya menolak jalan selama masih ada sesi D8
atau jawaban manual. Jadi fixture menghapus semua yang dibuatnya sesudah tes, lulus atau
gagal. Riwayat jawaban dan audit tidak bisa dihapus (trigger append-only), jadi penghapusan
memakai session_replication_role = replica, yang mematikan trigger untuk koneksi itu saja
(butuh superuser, seperti container tes dan CI; pola yang sama dengan
test_rubric_activation_race_pg.py).
"""

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.modules.runs.dependencies import get_manual_answer_service, get_manual_session_service
from app.modules.runs.models import AnswerHistory, Attempt
from app.modules.runs.repository import ManualSessionRepository
from app.modules.runs.schemas import ManualAnswerSaveRequest, ManualStartRequest
from app.shared.exceptions import ConfirmationRequiredError
from tests.judge_pg import buat_penilai
from tests.measurement_pg import buat_admin, buat_kasus

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

BATAS_TUNGGU = 10.0


@pytest.fixture
def engine():
    mesin = create_engine(
        POSTGRES_URL, pool_size=5, connect_args={"options": "-c lock_timeout=5000"}
    )
    yield mesin
    mesin.dispose()


@pytest.fixture
def buat_db(engine):
    return sessionmaker(bind=engine)


def _pengisi(conn, nama: str) -> uuid.UUID:
    """Satu orang tim legal (Kontributor) yang benar-benar ada di tabel users."""
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, name, role) VALUES (:id, :email, :nama, 'contributor')"
        ),
        {"id": user_id, "email": f"{nama.lower()}-{user_id.hex[:8]}@veritask.test", "nama": nama},
    )
    return user_id


def _hapus_jejak(engine, *, sesi_id, produk, penilai, kasus, suite, pengguna) -> None:
    """Hapus sesi beserta snapshot, percobaan, riwayat, audit, dan data pendukungnya."""
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        per_sesi = {"s": sesi_id}
        if sesi_id is not None:
            for perintah in (
                "DELETE FROM answer_history WHERE attempt_id IN "
                "(SELECT id FROM attempts WHERE session_id = :s)",
                "DELETE FROM attempts WHERE session_id = :s",
                "DELETE FROM case_snapshot_items WHERE snapshot_id IN "
                "(SELECT id FROM case_snapshots WHERE session_id = :s)",
                "DELETE FROM case_snapshots WHERE session_id = :s",
                "DELETE FROM measurement_session_suites WHERE session_id = :s",
                "DELETE FROM session_product_copies WHERE session_id = :s",
                "DELETE FROM audit_logs WHERE entity_id = :s",
                "DELETE FROM measurement_sessions WHERE id = :s",
            ):
                conn.execute(text(perintah), per_sesi)
        conn.execute(text("DELETE FROM ai_products WHERE id = :id"), {"id": produk})
        conn.execute(text("DELETE FROM cases WHERE id = :id"), {"id": kasus})
        conn.execute(text("DELETE FROM case_versions WHERE case_id = :id"), {"id": kasus})
        conn.execute(text("DELETE FROM suites WHERE id = :id"), {"id": suite})
        conn.execute(text("DELETE FROM judge_providers WHERE id = :id"), {"id": penilai})
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": list(pengguna)})


@pytest.fixture
def sesi(engine, buat_db):
    """Sesi manual berisi satu kasus dan satu percobaan, dibuat lewat service asli, sudah commit.

    Mengembalikan (session_id, snapshot_item_id, rina, budi). Semua yang dibuat dihapus lagi
    sesudah tes (lihat docstring modul).
    """
    with engine.begin() as conn:
        admin = buat_admin(conn)
        case_id, _ = buat_kasus(conn, admin, "dev")
        suite_id = conn.scalar(text("SELECT suite_id FROM cases WHERE id = :id"), {"id": case_id})
        produk = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO ai_products (id, name, created_by, measurement_type, vendor_name, site_url) "
                "VALUES (:id, :nama, :admin, 'manual', 'LegalHero', 'https://legalhero.test')"
            ),
            {"id": produk, "nama": f"LegalHero QA {produk.hex[:8]}", "admin": admin},
        )
        rina, budi = _pengisi(conn, "Rina"), _pengisi(conn, "Budi")
        # Sejak #128 (SCRUM-269) sesi manual wajib punya provider penilai.
        penilai = buat_penilai(conn, admin)

    sesi_id = None
    try:
        with buat_db() as db:
            dibuat = get_manual_session_service(db).create(
                ManualStartRequest(
                    label="QA balapan jawaban",
                    purpose="trial",
                    product_id=produk,
                    suite_ids=[suite_id],
                    case_filter="dev",
                    repeat_count=1,
                    judge_provider_id=penilai,
                ),
                created_by=admin,
            )
            db.commit()
            sesi_id = dibuat.session_id
            item_id = db.scalar(
                select(Attempt.snapshot_item_id).where(Attempt.session_id == sesi_id)
            )
        yield sesi_id, item_id, rina, budi
    finally:
        _hapus_jejak(
            engine,
            sesi_id=sesi_id,
            produk=produk,
            penilai=penilai,
            kasus=case_id,
            suite=suite_id,
            pengguna=(admin, rina, budi),
        )


def _permintaan(item_id, teks: str, *, konfirmasi: bool) -> ManualAnswerSaveRequest:
    return ManualAnswerSaveRequest(
        snapshot_item_id=item_id, attempt_no=1, answer_md=teks, confirm=konfirmasi
    )


def _tunggu_kunci(engine, pid: int) -> None:
    """Tunggu sampai backend pid benar-benar menunggu kunci (bukan sekadar lambat)."""
    batas = time.monotonic() + BATAS_TUNGGU
    with engine.connect() as pantau:
        while time.monotonic() < batas:
            jenis = pantau.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
            )
            pantau.rollback()
            if jenis == "Lock":
                return
            time.sleep(0.02)
    pytest.fail(f"backend {pid} tidak pernah menunggu kunci")


def _balapan(buat_db, engine, monkeypatch, sesi_id, pertama, kedua):
    """Jalankan dua simpan: `pertama` memegang kunci dan ditahan sebelum menulis, `kedua`
    dimulai dan harus menunggu kunci, lalu `pertama` dilepas.

    pertama/kedua = (actor, ManualAnswerSaveRequest). Mengembalikan {"A": hasil, "B": hasil},
    hasilnya respons atau exception.
    """
    memegang_kunci, lanjut = threading.Event(), threading.Event()
    tulis_asli = ManualSessionRepository.write_answer

    def tulis_tertahan(self, *args, **kwargs):
        if threading.current_thread().name == "A":
            memegang_kunci.set()
            assert lanjut.wait(BATAS_TUNGGU)
        return tulis_asli(self, *args, **kwargs)

    monkeypatch.setattr(ManualSessionRepository, "write_answer", tulis_tertahan)
    hasil: dict[str, object] = {}
    pid_b: dict[str, int] = {}

    def simpan(nama, actor, permintaan):
        with buat_db() as db:
            if nama == "B":
                pid_b["pid"] = db.connection().scalar(text("SELECT pg_backend_pid()"))
                siap_b.set()
            try:
                hasil[nama] = get_manual_answer_service(db).save(sesi_id, permintaan, actor=actor)
            except Exception as galat:  # noqa: BLE001 - hasil tiap thread dibandingkan di tes
                hasil[nama] = galat

    siap_b = threading.Event()
    a = threading.Thread(target=simpan, name="A", args=("A", *pertama))
    b = threading.Thread(target=simpan, name="B", args=("B", *kedua))
    a.start()
    assert memegang_kunci.wait(BATAS_TUNGGU)
    b.start()
    assert siap_b.wait(BATAS_TUNGGU)
    _tunggu_kunci(engine, pid_b["pid"])
    lanjut.set()
    a.join(BATAS_TUNGGU)
    b.join(BATAS_TUNGGU)
    return hasil


def _keadaan(buat_db, sesi_id, item_id):
    """(jawaban sekarang, pengisinya, riwayat teks lama urut waktu)."""
    with buat_db() as db:
        percobaan = db.scalars(
            select(Attempt).where(
                Attempt.session_id == sesi_id, Attempt.snapshot_item_id == item_id
            )
        ).one()
        lama = db.scalars(
            select(AnswerHistory.answer_md)
            .where(AnswerHistory.attempt_id == percobaan.id)
            .order_by(AnswerHistory.overwritten_at, AnswerHistory.id)
        ).all()
        return percobaan.answer_text, percobaan.filled_by, list(lama)


def test_dua_jawaban_pertama_bersamaan_yang_kedua_diminta_konfirmasi(
    buat_db, engine, monkeypatch, sesi
):
    """Edge: AC5, Rina dan Budi mengisi slot kosong yang sama pada saat bersamaan tanpa
    konfirmasi. Budi menunggu kunci, lalu melihat jawaban Rina dan ditolak 409; jawaban Rina
    utuh dan tidak ada riwayat palsu."""
    # Arrange
    sesi_id, item_id, rina, budi = sesi

    # Act
    hasil = _balapan(
        buat_db,
        engine,
        monkeypatch,
        sesi_id,
        (rina, _permintaan(item_id, "Jawaban Rina", konfirmasi=False)),
        (budi, _permintaan(item_id, "Jawaban Budi", konfirmasi=False)),
    )

    # Assert
    assert not isinstance(hasil["A"], Exception), hasil["A"]
    assert isinstance(hasil["B"], ConfirmationRequiredError), hasil["B"]
    assert _keadaan(buat_db, sesi_id, item_id) == ("Jawaban Rina", rina, [])


def test_dua_timpa_bersamaan_dengan_konfirmasi_tidak_menghilangkan_versi(
    buat_db, engine, monkeypatch, sesi
):
    """Edge: AC5, dua orang menimpa jawaban yang sama bersamaan, keduanya sudah konfirmasi.
    Timpa kedua menunggu yang pertama, jadi riwayat berisi versi asli lalu versi Rina, dan
    jawaban akhirnya milik Budi. Tidak ada versi yang hilang di tengah."""
    # Arrange
    sesi_id, item_id, rina, budi = sesi
    with buat_db() as db:
        get_manual_answer_service(db).save(
            sesi_id, _permintaan(item_id, "Versi asli", konfirmasi=False), actor=budi
        )

    # Act
    hasil = _balapan(
        buat_db,
        engine,
        monkeypatch,
        sesi_id,
        (rina, _permintaan(item_id, "Versi Rina", konfirmasi=True)),
        (budi, _permintaan(item_id, "Versi Budi", konfirmasi=True)),
    )

    # Assert
    assert not any(isinstance(h, Exception) for h in hasil.values()), hasil
    assert _keadaan(buat_db, sesi_id, item_id) == ("Versi Budi", budi, ["Versi asli", "Versi Rina"])
