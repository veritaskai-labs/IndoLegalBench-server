"""Penilaian per LLM judge dan riwayatnya. PBI-14 AC6, AC7, SCRUM-78 SCRUM-280.

Feedback UAT klien 9 Nov (K-17, K-18): satu pengukuran bisa dinilai beberapa LLM judge,
nilai ulang tidak menimpa hasil lama, dan setiap hasil membawa judge yang dipakai.

Isolasi: build_judge diganti lewat monkeypatch, karena penilai sungguhan butuh kredensial
dan memakai token berbayar. Kurs dipasang tetap lewat dependency override.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.auth.models import User
from app.modules.grading.models import GradingResult, GradingRun, LlmJudgeResult
from app.modules.runs.dependencies import get_exchange_rate
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.modules.runs.models import SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _manual, _otomatis
from tests.modules.runs.test_session_grade import (
    _BALASAN_PENILAI,
    _MockJudgeAsker,
    _setup_sesi_lengkap,
)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-judge-run@veritask.test", name="Admin Judge", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Diukur Judge")
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def penilai(db_session):
    """Dua produk otomatis aktif yang boleh dipilih sebagai LLM judge."""
    a = _otomatis("Penilai A")
    b = _otomatis("Penilai B")
    db_session.add_all([a, b])
    db_session.commit()
    return SimpleNamespace(a=a, b=b)


class _PenilaiBerversi(_MockJudgeAsker):
    """Penilai tiruan yang melaporkan versi modelnya, seperti ProviderResponse asli."""

    def __init__(self, model_version: str, **kwargs) -> None:
        super().__init__(text=_BALASAN_PENILAI, **kwargs)
        self.model_version = model_version


def _ikatan(judge_id: uuid.UUID, asker: object) -> SimpleNamespace:
    return SimpleNamespace(
        judge_provider_id=judge_id,
        asker=asker,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000000"),
    )


@pytest.fixture
def pasang_penilai(db_session, penilai, monkeypatch):
    """Sesi memakai Penilai A; build_judge mengembalikan asker milik id yang diminta."""

    def pasang(sesi, asker_a=None, asker_b=None):
        sesi.judge_provider_id = penilai.a.id
        db_session.commit()
        ikatan = {
            penilai.a.id: _ikatan(penilai.a.id, asker_a or _PenilaiBerversi("model-a-2026")),
            penilai.b.id: _ikatan(penilai.b.id, asker_b or _PenilaiBerversi("model-b-2026")),
        }
        monkeypatch.setattr(
            "app.modules.providers.judge_service.build_judge", lambda db, j: ikatan[j]
        )
        app.dependency_overrides[get_exchange_rate] = lambda: ConfiguredExchangeRate(
            Decimal("16000")
        )

    return pasang


def _grade(sesi_id: uuid.UUID) -> str:
    return f"/measurements/{sesi_id}/grade"


def _riwayat(sesi_id: uuid.UUID) -> str:
    return f"/measurements/{sesi_id}/grading-runs"


def test_dua_judge_berbeda_menghasilkan_dua_run_dengan_judge_masing_masing(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: K-17, jawaban sama dinilai dua judge, keduanya tersimpan sebagai run sendiri."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.a.id)})
    admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Act
    baris = admin.get(_riwayat(sesi.id)).json()

    # Assert
    assert [b["judge"]["judge_provider_id"] for b in baris] == [
        str(penilai.b.id),
        str(penilai.a.id),
    ]
    assert [b["judge"]["name"] for b in baris] == ["Penilai B", "Penilai A"]
    assert [b["judge"]["model_version"] for b in baris] == ["model-b-2026", "model-a-2026"]
    assert [b["is_latest"] for b in baris] == [True, False]


def test_respons_nilai_membawa_judge_model_dan_total_biaya(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: hasil penilaian menyebut judge, model yang menjawab, dan total biaya judge."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Assert
    assert res.status_code == 200
    judge = res.json()["judge"]
    run_id = uuid.UUID(res.json()["grading_run_id"])
    total = db_session.scalar(
        select(func.sum(LlmJudgeResult.cost_idr))
        .join(GradingResult, GradingResult.id == LlmJudgeResult.grading_result_id)
        .where(GradingResult.grading_run_id == run_id)
    )
    assert judge["judge_provider_id"] == str(penilai.b.id)
    assert judge["name"] == "Penilai B"
    assert judge["model_version"] == "model-b-2026"
    assert total is not None and total > 0
    assert Decimal(judge["cost_idr"]) == total


def test_nilai_ulang_dengan_judge_sama_menambah_run_tanpa_menimpa(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: K-18, nilai ulang append; run pertama tetap ada dengan skor yang sama."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    pertama = admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.a.id)}).json()

    # Act
    admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.a.id)})

    # Assert
    baris = admin.get(_riwayat(sesi.id)).json()
    assert len(baris) == 2
    lama = next(b for b in baris if b["grading_run_id"] == pertama["grading_run_id"])
    assert lama["scores"] == pertama["scores"]
    assert lama["is_latest"] is False
    assert [b["kind"] for b in baris] == ["regrade", "initial"]


def test_tanpa_judge_di_permintaan_memakai_judge_sesi(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Edge: permintaan lama tanpa judge_provider_id tetap dinilai judge milik sesi."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json={})

    # Assert
    assert res.status_code == 200
    assert res.json()["judge"]["judge_provider_id"] == str(penilai.a.id)
    run = db_session.get(GradingRun, uuid.UUID(res.json()["grading_run_id"]))
    assert run.judge_provider_id == penilai.a.id


def test_sesi_tanpa_judge_dinilai_dengan_judge_pilihan_admin(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: K-14 (SCRUM-281), sesi dibuat tanpa judge; judge dipilih di langkah penilaian."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Assert
    assert res.status_code == 200, res.text
    assert res.json()["judge"]["judge_provider_id"] == str(penilai.b.id)


@pytest.mark.parametrize("badan", [{}, {"judge_provider_id": None}])
def test_tanpa_judge_di_permintaan_maupun_sesi_ditolak_422_tanpa_run(
    badan, as_role, db_session, produk, pembuat, pasang_penilai
):
    """Negatif: K-14 (SCRUM-281), tanpa judge sama sekali penilaian ditolak sebelum ada
    panggilan atau run, bukan run yang semua percobaannya gagal judge_not_configured."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json=badan)

    # Assert
    assert res.status_code == 422
    assert res.json()["code"] == "JUDGE_REQUIRED"
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == 0


@pytest.mark.parametrize("jenis", ["manual", "nonaktif", "tidak_ada"])
def test_judge_tidak_layak_ditolak_422_tanpa_membuat_run(
    jenis, as_role, db_session, produk, pembuat, pasang_penilai
):
    """Negatif: judge harus produk otomatis yang aktif; ditolak sebelum ada panggilan atau run."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    if jenis == "tidak_ada":
        judge_id = uuid.uuid4()
    else:
        item = (
            _manual("Penilai Manual")
            if jenis == "manual"
            else _otomatis("Penilai Nonaktif", is_active=False)
        )
        db_session.add(item)
        db_session.commit()
        judge_id = item.id

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json={"judge_provider_id": str(judge_id)})

    # Assert
    assert res.status_code == 422
    assert res.json()["code"] == "JUDGE_NOT_ELIGIBLE"
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == 0


def test_judge_gagal_di_semua_panggilan_tanpa_model_dan_biaya(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Edge: tidak ada jawaban judge, jadi model dan biaya kosong, judge tetap tercatat."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    gagal = _PenilaiBerversi("", error="upstream down", failure_kind="provider_error")
    gagal.input_tokens = None
    gagal.output_tokens = None
    pasang_penilai(sesi, asker_b=gagal)

    # Act
    res = as_role(Role.ADMIN).post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Assert
    judge = res.json()["judge"]
    assert judge["judge_provider_id"] == str(penilai.b.id)
    assert judge["model_version"] is None
    assert judge["cost_idr"] is None


def test_detail_run_lama_bisa_dibuka_lengkap_dengan_judgenya(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: hasil judge lain tetap bisa dibuka walau bukan yang terbaru."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    admin = as_role(Role.ADMIN)
    pertama = admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.a.id)}).json()
    admin.post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Act
    res = admin.get(f"{_riwayat(sesi.id)}/{pertama['grading_run_id']}")

    # Assert
    assert res.status_code == 200
    body = res.json()
    assert body["grading_run_id"] == pertama["grading_run_id"]
    assert body["is_latest"] is False
    assert body["judge"]["judge_provider_id"] == str(penilai.a.id)
    assert len(body["cases"]) == 2


def test_detail_run_dari_sesi_lain_ditolak_404(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Negatif: id run sesi lain tidak bisa dibuka lewat sesi ini (cegah IDOR, OWASP A01)."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    lain = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(lain)
    admin = as_role(Role.ADMIN)
    run_lain = admin.post(_grade(lain.id), json={}).json()["grading_run_id"]

    # Act
    res = admin.get(f"{_riwayat(sesi.id)}/{run_lain}")

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "GRADING_RUN_NOT_FOUND"


def test_detail_run_sesi_tidak_ada_ditolak_404(as_role):
    """Negatif: sesi yang tidak ada dijawab MEASUREMENT_NOT_FOUND, bukan 500."""
    # Arrange
    url = f"{_riwayat(uuid.uuid4())}/{uuid.uuid4()}"

    # Act
    res = as_role(Role.ADMIN).get(url)

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_detail_run_developer_ai_ditolak_403(as_role, db_session, produk, pembuat):
    """Negatif: Developer AI tidak ikut membaca Laporan, jadi detail run tetap tertutup."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    res = as_role(Role.DEVELOPER_AI).get(f"{_riwayat(sesi.id)}/{uuid.uuid4()}")

    # Assert
    assert res.status_code == 403


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.CONTRIBUTOR])
def test_detail_run_dibuka_pembaca_laporan_dengan_jawaban_disamarkan(
    peran, as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: baris Laporan bisa dibuka Viewer dan Kontributor, tanpa soal dan jawaban mentah."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)
    run_id = as_role(Role.ADMIN).post(_grade(sesi.id), json={}).json()["grading_run_id"]

    # Act
    res = as_role(peran).get(f"{_riwayat(sesi.id)}/{run_id}")

    # Assert
    assert res.status_code == 200
    body = res.json()
    assert body["grading_run_id"] == run_id
    assert all(kasus["question"] is None for kasus in body["cases"])
    assert all(
        percobaan["answer_text"] is None
        for kasus in body["cases"]
        for percobaan in kasus["attempts"]
    )


def test_detail_run_pembaca_laporan_dari_sesi_lain_ditolak_404(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Negatif: pembaca Laporan juga tidak bisa membuka run sesi lain lewat sesi ini."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    lain = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(lain)
    run_lain = as_role(Role.ADMIN).post(_grade(lain.id), json={}).json()["grading_run_id"]

    # Act
    res = as_role(Role.VIEWER).get(f"{_riwayat(sesi.id)}/{run_lain}")

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "GRADING_RUN_NOT_FOUND"


def test_detail_run_id_bukan_uuid_ditolak_422(as_role, db_session, produk, pembuat):
    """Edge: id run yang bukan UUID ditolak validasi path, bukan 500."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)

    # Act
    res = as_role(Role.VIEWER).get(f"{_riwayat(sesi.id)}/bukan-uuid")

    # Assert
    assert res.status_code == 422


def test_audit_penilaian_mencatat_judge_yang_dipakai(
    as_role, db_session, produk, pembuat, penilai, pasang_penilai
):
    """Positif: jejak audit menyebut judge, supaya bisa ditelusuri siapa yang menilai."""
    # Arrange
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(sesi)

    # Act
    as_role(Role.ADMIN).post(_grade(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Assert
    log = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == AuditEntityType.MEASUREMENT_SESSION,
            AuditLog.entity_id == sesi.id,
        )
    ).one()
    assert log.after["judge_provider_id"] == str(penilai.b.id)
