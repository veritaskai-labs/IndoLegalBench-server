"""QA jalur deterministik dengan penilai LLM dipalsukan. PBI-14 AC1; SCRUM-229.

Subtask meminta "pytest jalur deterministik dengan mock judge". Yang dibuktikan di sini
bukan perhitungannya, itu sudah dijaga test_scoring_legal_examples_qa.py, melainkan
kemandiriannya: apa pun yang dijawab penilai LLM, angka deterministik untuk jawaban yang
sama tidak bergeser sedikit pun.

Satu run juga harus memakai satu versi rubrik untuk seluruh percobaan, supaya jawaban
otomatis dan manual dinilai dengan rubrik dan versi yang sama.

Label: Positif = perilaku yang diharapkan, Edge = batas perilaku.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    SessionStatus,
)
from app.modules.runs.session_grade import grade_session
from app.shared.security import Role
from tests.modules.runs.test_session_grade import (
    _MockJudgeAsker,
    _setup_sesi_lengkap,
    pembuat,  # noqa: F401
    produk,  # noqa: F401
)

BALASAN_BAGUS = (
    "CRITERION: badan hukum\nMET: yes\n"
    "CRITERION: pesangon\nMET: yes\n"
    "CRITERION: Conclusion: sah\nMET: yes\n"
)
BALASAN_BURUK = (
    "CRITERION: badan hukum\nMET: no\n"
    "CRITERION: pesangon\nMET: no\n"
    "EXPLANATION: tidak ada dasar hukum yang disebut\n"
    "CRITERION: Conclusion: sah\nMET: no\n"
)


def _nilai(db_session, sesi, pembuat_sesi, asker):
    return grade_session(
        db_session,
        sesi.id,
        actor_id=pembuat_sesi.id,
        actor_role=Role.ADMIN,
        judge_asker=asker,
    )


def _skor_deterministik(respons) -> dict[str, Decimal | None]:
    return {
        kasus.case_code: kasus.attempts[0].score.deterministic
        for kasus in respons.cases
        if kasus.attempts and kasus.attempts[0].score is not None
    }


def test_balasan_penilai_tidak_menggeser_skor_deterministik(db_session, produk, pembuat):  # noqa: F811
    """Positif: penilai memuji atau mencela, angka deterministik tetap sama (AC1)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    bagus = _skor_deterministik(_nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS)))
    buruk = _skor_deterministik(_nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BURUK)))

    assert bagus == buruk
    assert bagus, "jalur deterministik harus menghasilkan angka, bukan kosong"


def test_penilai_yang_gagal_membuat_seluruh_percobaan_tanpa_skor(db_session, produk, pembuat):  # noqa: F811
    """Edge: penilai error melewati percobaan sepenuhnya, termasuk jalur deterministiknya.

    Perilaku yang disengaja (AC4): percobaannya ditandai gangguan teknis, tidak diberi skor,
    dan dilewati dari rata-rata, bukan dihitung nol, jadi angka produk tidak ikut turun.
    Pemulihannya lewat coba ulang manual (PBI-12) atau nilai ulang. Dikunci di sini supaya
    perubahan pada aturan itu terlihat, bukan karena perilakunya dipertanyakan.
    """

    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    normal = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS))
    gagal = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(raise_exc=True))

    assert _skor_deterministik(normal), "jalur normal harus punya angka deterministik"
    assert _skor_deterministik(gagal) == {}
    assert all(
        percobaan.is_technical_disruption for kasus in gagal.cases for percobaan in kasus.attempts
    )


def test_penilaian_sama_dua_kali_identik(db_session, produk, pembuat):  # noqa: F811
    """Positif: menilai ulang jawaban yang sama memberi angka yang sama persis (AC1)."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    pertama = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS))
    kedua = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS))

    assert _skor_deterministik(pertama) == _skor_deterministik(kedua)


def _tambah_percobaan_manual(db_session, sesi, pengisi, jawaban: str) -> None:
    """Percobaan kedua pada kasus dev, diisi tangan (AttemptSource.MANUAL, PBI-22).

    _setup_sesi_lengkap hanya membuat percobaan otomatis, jadi tanpa ini tidak ada jawaban
    manual untuk dibandingkan dan aturan "satu rubrik untuk semua percobaan" tidak terbukti.
    """
    item_dev = next(
        item
        for item in db_session.scalars(
            select(CaseSnapshotItem)
            .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
            .where(CaseSnapshot.session_id == sesi.id)
        )
        if item.body["sections"]["case_code"] == "DEV-01"
    )
    db_session.add(
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item_dev.id,
            repetition_no=2,
            source=AttemptSource.MANUAL,
            status=AttemptStatus.SUCCEEDED,
            answer_text=jawaban,
            filled_by=pengisi.id,
            filled_at=datetime.now(UTC),
        )
    )
    db_session.commit()


def test_satu_run_memakai_satu_versi_rubrik_untuk_percobaan_otomatis_dan_manual(
    db_session,
    produk,  # noqa: F811
    pembuat,  # noqa: F811
):
    """Positif: jawaban otomatis dan manual masuk satu run dengan satu versi rubrik (AC9).

    Dibuktikan dengan sesi yang benar-benar memuat kedua sumber, bukan hanya dengan
    memastikan rubric_version tidak kosong: versi yang sama tidak membuktikan apa pun
    kalau yang dinilai cuma jawaban otomatis.
    """
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    _tambah_percobaan_manual(
        db_session,
        sesi,
        pembuat,
        "Berdasarkan Pasal 156 angka 1 Undang-Undang Nomor 40 Tahun 2007 tentang Perseroan "
        "Terbatas, badan hukum pesangon adalah sah.",
    )

    respons = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS))

    semua = [percobaan for kasus in respons.cases for percobaan in kasus.attempts]
    sumber = {percobaan.source for percobaan in semua}
    assert sumber == {"automatic", "manual"}, f"kedua sumber harus ikut dinilai, dapat {sumber}"
    assert respons.rubric_version
    assert all(not percobaan.is_technical_disruption for percobaan in semua)
    manual = [p for p in semua if p.source == "manual"]
    otomatis = [p for p in semua if p.source == "automatic"]
    assert manual and otomatis
    assert all(p.score is not None for p in manual + otomatis), (
        "jawaban manual dan otomatis sama-sama harus bernilai di run yang sama"
    )
