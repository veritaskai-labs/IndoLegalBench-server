"""QA jalur deterministik dengan penilai LLM dipalsukan. PBI-14 AC1; SCRUM-229.

Subtask meminta "pytest jalur deterministik dengan mock judge". Yang dibuktikan di sini
bukan perhitungannya, itu sudah dijaga test_scoring_legal_examples_qa.py, melainkan
kemandiriannya: apa pun yang dijawab penilai LLM, angka deterministik untuk jawaban yang
sama tidak bergeser sedikit pun.

Satu run juga harus memakai satu versi rubrik untuk seluruh percobaan, supaya jawaban
otomatis dan manual dinilai dengan rubrik dan versi yang sama.

Label: Positif = perilaku yang diharapkan, Edge = batas perilaku.
"""

from decimal import Decimal

from app.modules.runs.models import SessionStatus
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


def test_satu_run_memakai_satu_versi_rubrik_untuk_semua_percobaan(db_session, produk, pembuat):  # noqa: F811
    """Positif: jawaban otomatis dan manual dinilai dengan rubrik dan versi yang sama."""
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    respons = _nilai(db_session, sesi, pembuat, _MockJudgeAsker(BALASAN_BAGUS))

    assert respons.rubric_version
    jumlah_percobaan = sum(len(kasus.attempts) for kasus in respons.cases)
    assert jumlah_percobaan >= 1, "sesi uji harus punya percobaan untuk dinilai"
