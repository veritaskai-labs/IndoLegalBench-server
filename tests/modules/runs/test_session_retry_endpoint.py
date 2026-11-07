"""POST /measurements/{id}/retry dan detail teknis upaya panggilan. SCRUM-77 SCRUM-218.

PBI-12 AC3 dan D5 langkah 27-38: Admin (dan Developer AI untuk sesi miliknya,
D10 [USULAN PO]) mencoba ulang percobaan gagal; sesi berjalan lagi dan runner
memanggil ulang. Tombol "lihat detail teknis" membuka setiap upaya di call_logs
(try_no, coba ulang manual, waktu, jenis kegagalan, respons asli penyedia).

D10 bagian C: detail teknis error kasus test disamarkan untuk Developer AI;
yang tampil hanya jenis kegagalan dan waktunya (D5 langkah 37).

Label: Positif = berhasil sesuai peran, Negatif = ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.auth.models import User
from app.modules.cases.sections import snapshot_body
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionStatus,
)
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

JAM = datetime(2026, 11, 5, 4, 0, tzinfo=UTC)
RAW_PENYEDIA = {"error": {"code": 503, "message": "overloaded"}}


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-retry-api@veritask.test", name="Admin Retry", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Retry API")
    db_session.add(item)
    db_session.commit()
    return item


def _kasus(db_session, sesi, snapshot, split_tag: str, statuses: list[AttemptStatus]) -> list:
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body=snapshot_body(
            case_code=f"UJI-{split_tag}",
            version_no=1,
            status="approved",
            split_tag=split_tag,
            content={"title": f"Kasus {split_tag}", "question": "Apa dasar hukumnya?"},
            legal_refs=[],
        ),
    )
    db_session.add(item)
    db_session.flush()
    percobaan = [
        Attempt(
            session_id=sesi.id,
            snapshot_item_id=item.id,
            repetition_no=nomor,
            source=AttemptSource.AUTOMATIC,
            status=status,
        )
        for nomor, status in enumerate(statuses, 1)
    ]
    db_session.add_all(percobaan)
    db_session.flush()
    return percobaan


def _upaya(db_session, attempt: Attempt, sesi: MeasurementSession, try_no: int) -> None:
    mulai = JAM + timedelta(minutes=try_no)
    db_session.add(
        CallLog(
            attempt_id=attempt.id,
            product_id=sesi.product_id,
            started_at=mulai,
            ended_at=mulai + timedelta(seconds=240),
            duration_ms=240000,
            usd_idr_rate=Decimal("16000"),
            model_version="model-uji-2026",
            result="no_response",
            failure_kind="no_response",
            raw={**RAW_PENYEDIA, "_execution": {"error": "timeout 240 s", "answer_text": ""}},
            try_no=try_no,
            is_manual_retry=try_no > 1,
        )
    )


def _sesi_gagal(db_session, produk, created_by) -> tuple[MeasurementSession, Attempt, Attempt]:
    """Sesi Selesai dengan kegagalan: satu percobaan gagal di kasus dev dan satu di kasus test."""
    sesi = _sesi(db_session, produk, created_by, status=SessionStatus.FINISHED_WITH_FAILURES)
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()
    gagal_dev, _ = _kasus(
        db_session,
        sesi,
        snapshot,
        "dev",
        [AttemptStatus.TECHNICAL_FAILURE, AttemptStatus.SUCCEEDED],
    )
    gagal_test, _ = _kasus(
        db_session,
        sesi,
        snapshot,
        "test",
        [AttemptStatus.TECHNICAL_FAILURE, AttemptStatus.SUCCEEDED],
    )
    for attempt in (gagal_dev, gagal_test):
        _upaya(db_session, attempt, sesi, 1)
        _upaya(db_session, attempt, sesi, 2)
    db_session.commit()
    return sesi, gagal_dev, gagal_test


@pytest.fixture
def sesi_gagal(db_session, produk, pembuat):
    return _sesi_gagal(db_session, produk, pembuat)


def _retry(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/retry"


def _calls(session_id: uuid.UUID, attempt_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}/attempts/{attempt_id}/calls"


def test_admin_mencoba_ulang_semua_yang_gagal(as_role, db_session, sesi_gagal):
    """Positif: 202, sesi running, dua percobaan gagal kembali pending untuk runner."""
    # Arrange
    sesi, gagal_dev, gagal_test = sesi_gagal

    # Act
    response = as_role(Role.ADMIN).post(_retry(sesi.id), json={"all_failed": True})

    # Assert
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert sorted(body["retried_attempt_ids"]) == sorted([str(gagal_dev.id), str(gagal_test.id)])
    db_session.expire_all()
    assert db_session.get(MeasurementSession, sesi.id).status == SessionStatus.RUNNING


def test_admin_mencoba_ulang_satu_kasus(as_role, db_session, sesi_gagal):
    """Positif: centang per kasus, hanya percobaan itu yang dibuka."""
    # Arrange
    sesi, gagal_dev, gagal_test = sesi_gagal

    # Act
    response = as_role(Role.ADMIN).post(_retry(sesi.id), json={"attempt_ids": [str(gagal_dev.id)]})

    # Assert
    assert response.status_code == 202
    assert response.json()["retried_attempt_ids"] == [str(gagal_dev.id)]
    db_session.expire_all()
    assert db_session.get(Attempt, gagal_test.id).status == AttemptStatus.TECHNICAL_FAILURE


def test_developer_ai_mencoba_ulang_sesi_miliknya(as_role, db_session, produk):
    """Positif: D10 [USULAN PO], Developer AI mengendalikan sesi yang ia buat sendiri."""
    # Arrange
    client = as_role(Role.DEVELOPER_AI)
    # Pembuat sesi = pengguna yang login lewat as_role (USER_ID_QA).
    sesi, _, _ = _sesi_gagal(db_session, produk, SimpleNamespace(id=USER_ID_QA))

    # Act
    response = client.post(_retry(sesi.id), json={"all_failed": True})

    # Assert
    assert response.status_code == 202


def test_developer_ai_tidak_boleh_mencoba_ulang_sesi_orang_lain(as_role, sesi_gagal):
    """Negatif: D10, sesi buatan Admin ditolak 403 NOT_YOUR_SESSION."""
    # Arrange
    sesi, _, _ = sesi_gagal

    # Act
    response = as_role(Role.DEVELOPER_AI).post(_retry(sesi.id), json={"all_failed": True})

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_YOUR_SESSION"


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_tidak_boleh_mencoba_ulang(as_role, sesi_gagal, peran):
    """Negatif: D10, kontrol sesi otomatis hanya Admin dan Developer AI."""
    # Arrange
    sesi, _, _ = sesi_gagal

    # Act
    response = as_role(peran).post(_retry(sesi.id), json={"all_failed": True})

    # Assert
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body", [{}, {"attempt_ids": []}, {"attempt_ids": ["bukan-uuid"]}, {"all_failed": False}]
)
def test_body_tanpa_pilihan_yang_jelas_ditolak_422(as_role, sesi_gagal, body):
    """Negatif: pilih per kasus atau semua, tepat salah satu."""
    # Arrange
    sesi, _, _ = sesi_gagal

    # Act
    response = as_role(Role.ADMIN).post(_retry(sesi.id), json=body)

    # Assert
    assert response.status_code == 422


def test_admin_melihat_setiap_upaya_dengan_respons_asli_penyedia(as_role, sesi_gagal):
    """Positif: D5 langkah 38, upaya urut try_no, coba ulang manual terlihat, raw utuh."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal

    # Act
    response = as_role(Role.ADMIN).get(_calls(sesi.id, gagal_dev.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["attempt_id"] == str(gagal_dev.id)
    assert body["status"] == "technical_failure"
    assert body["masked"] is False
    assert [(c["try_no"], c["is_manual_retry"]) for c in body["calls"]] == [(1, False), (2, True)]
    pertama = body["calls"][0]
    assert pertama["failure_kind"] == "no_response"
    assert pertama["error_summary"] == "timeout 240 s"
    assert pertama["raw_response"]["error"] == RAW_PENYEDIA["error"]


def test_admin_melihat_detail_teknis_kasus_test(as_role, sesi_gagal):
    """Positif: D10, Admin melihat isi lengkap kasus test."""
    # Arrange
    sesi, _, gagal_test = sesi_gagal

    # Act
    body = as_role(Role.ADMIN).get(_calls(sesi.id, gagal_test.id)).json()

    # Assert
    assert body["masked"] is False
    assert body["calls"][0]["raw_response"] is not None


def test_developer_ai_melihat_detail_teknis_kasus_dev(as_role, sesi_gagal):
    """Positif: D10, kasus dev terbuka penuh untuk Developer AI."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal

    # Act
    body = as_role(Role.DEVELOPER_AI).get(_calls(sesi.id, gagal_dev.id)).json()

    # Assert
    assert body["masked"] is False
    assert body["calls"][0]["error_summary"] == "timeout 240 s"


def test_developer_ai_hanya_melihat_jenis_dan_waktu_untuk_kasus_test(as_role, sesi_gagal):
    """Negatif: D5 langkah 37 dan D10, raw_response dan error_summary kasus test disamarkan."""
    # Arrange
    sesi, _, gagal_test = sesi_gagal

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_calls(sesi.id, gagal_test.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["masked"] is True
    for upaya in body["calls"]:
        assert upaya["raw_response"] is None
        assert upaya["error_summary"] is None
        assert upaya["failure_kind"] == "no_response"
        assert upaya["started_at"] is not None
    assert "overloaded" not in response.text


def test_percobaan_tanpa_upaya_mengembalikan_daftar_kosong(as_role, db_session, sesi_gagal):
    """Edge: percobaan yang belum pernah dipanggil (pending) punya daftar upaya kosong."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal
    berhasil = (
        db_session.query(Attempt)
        .filter(Attempt.snapshot_item_id == gagal_dev.snapshot_item_id, Attempt.repetition_no == 2)
        .one()
    )

    # Act
    body = as_role(Role.ADMIN).get(_calls(sesi.id, berhasil.id)).json()

    # Assert
    assert body["status"] == "succeeded"
    assert body["calls"] == []


def test_percobaan_milik_sesi_lain_tidak_ditemukan(
    as_role, db_session, produk, pembuat, sesi_gagal
):
    """Negatif: id percobaan harus milik sesi di URL (404 ATTEMPT_NOT_FOUND)."""
    # Arrange
    sesi, _, _ = sesi_gagal
    _, asing, _ = _sesi_gagal(db_session, produk, pembuat)

    # Act
    response = as_role(Role.ADMIN).get(_calls(sesi.id, asing.id))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "ATTEMPT_NOT_FOUND"


def test_sesi_tidak_ada_404(as_role):
    """Negatif: sesi asing menjadi 404 MEASUREMENT_NOT_FOUND."""
    # Act
    response = as_role(Role.ADMIN).get(_calls(uuid.uuid4(), uuid.uuid4()))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_tidak_melihat_detail_teknis(as_role, sesi_gagal, peran):
    """Negatif: detail teknis hanya untuk peran yang memantau pengukuran (Admin, Developer AI)."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal

    # Act
    response = as_role(peran).get(_calls(sesi.id, gagal_dev.id))

    # Assert
    assert response.status_code == 403


def _jalankan_lagi(db_session, sesi: MeasurementSession, status: SessionStatus) -> None:
    sesi.status = status
    db_session.commit()


@pytest.mark.parametrize("status", [SessionStatus.RUNNING, SessionStatus.QUEUED])
def test_developer_ai_melihat_respons_mentah_kasus_dev_saat_sesi_belum_berakhir(
    as_role, db_session, sesi_gagal, status
):
    """Positif: AC9 jawaban kasus dev terbaca selama sesi berjalan, termasuk respons mentahnya.

    Aturan lama (review #99) menutupnya sampai sesi berakhir; sekarang hanya kasus
    test yang tertutup, apa pun status sesinya.
    """
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal
    _jalankan_lagi(db_session, sesi, status)

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_calls(sesi.id, gagal_dev.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["masked"] is False
    assert body["calls"][0]["raw_response"]["error"] == RAW_PENYEDIA["error"]
    assert body["calls"][0]["error_summary"] == "timeout 240 s"


@pytest.mark.parametrize("status", [SessionStatus.RUNNING, SessionStatus.QUEUED])
def test_developer_ai_tidak_melihat_respons_mentah_kasus_test_saat_sesi_belum_berakhir(
    as_role, db_session, sesi_gagal, status
):
    """Negatif: kasus test tetap tertutup di sesi berjalan, hanya jenis kegagalan dan waktu."""
    # Arrange
    sesi, _, gagal_test = sesi_gagal
    _jalankan_lagi(db_session, sesi, status)

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_calls(sesi.id, gagal_test.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["masked"] is True
    for upaya in body["calls"]:
        assert upaya["raw_response"] is None
        assert upaya["error_summary"] is None
        assert upaya["failure_kind"] == "no_response"
    assert "overloaded" not in response.text
    assert "timeout 240 s" not in response.text


def test_admin_tetap_melihat_respons_mentah_sesi_yang_berjalan(as_role, db_session, sesi_gagal):
    """Positif: aturan sesi belum berakhir hanya untuk Developer AI; Admin memantau penuh."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal
    _jalankan_lagi(db_session, sesi, SessionStatus.RUNNING)

    # Act
    body = as_role(Role.ADMIN).get(_calls(sesi.id, gagal_dev.id)).json()

    # Assert
    assert body["masked"] is False
    assert body["calls"][0]["raw_response"]["error"] == RAW_PENYEDIA["error"]


@pytest.mark.parametrize("sections", [{"case_code": "UJI-X"}, {"split_tag": "lain"}])
def test_developer_ai_tidak_melihat_kasus_dengan_tag_tidak_dikenal(
    as_role, db_session, produk, pembuat, sections
):
    """Edge: penyamaran fail-closed, hanya kasus dev yang terbuka untuk Developer AI (review #99)."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={"version_no": 1, "sections": sections},
    )
    db_session.add(item)
    db_session.flush()
    attempt = Attempt(
        session_id=sesi.id,
        snapshot_item_id=item.id,
        repetition_no=1,
        source=AttemptSource.AUTOMATIC,
        status=AttemptStatus.TECHNICAL_FAILURE,
    )
    db_session.add(attempt)
    db_session.flush()
    _upaya(db_session, attempt, sesi, 1)
    db_session.commit()

    # Act
    body = as_role(Role.DEVELOPER_AI).get(_calls(sesi.id, attempt.id)).json()

    # Assert
    assert body["masked"] is True
    assert body["calls"][0]["raw_response"] is None


def _percobaan_di_detail(client, session_id: uuid.UUID) -> list[dict]:
    """Semua percobaan dari GET /measurements/{id}, seperti yang dibaca FE."""
    detail = client.get(f"/measurements/{session_id}").json()
    return [percobaan for kasus in detail["cases"] for percobaan in kasus["attempts"]]


def test_id_percobaan_dari_detail_dipakai_untuk_coba_ulang_per_kasus(as_role, sesi_gagal):
    """Positif: FE SCRUM-219/220 mencentang percobaan dari detail sesi, bukan hanya all_failed."""
    # Arrange
    sesi, gagal_dev, _ = sesi_gagal
    client = as_role(Role.ADMIN)
    pilihan = next(
        percobaan["id"]
        for percobaan in _percobaan_di_detail(client, sesi.id)
        if percobaan["id"] == str(gagal_dev.id)
    )

    # Act
    response = client.post(_retry(sesi.id), json={"attempt_ids": [pilihan]})

    # Assert
    assert response.status_code == 202
    assert response.json()["retried_attempt_ids"] == [str(gagal_dev.id)]


def test_kasus_test_yang_disamarkan_tetap_membawa_id_percobaan(as_role, db_session, produk):
    """Edge: Developer AI tetap butuh id untuk coba ulang dan detail teknis; isinya tetap tertutup.

    id percobaan hanya UUID, tidak memuat isi kasus, jadi aman ikut keluar di kasus test.
    """
    # Arrange
    client = as_role(Role.DEVELOPER_AI)
    # Pembuat sesi = pengguna yang login lewat as_role (USER_ID_QA).
    sesi, _, gagal_test = _sesi_gagal(db_session, produk, SimpleNamespace(id=USER_ID_QA))

    # Act
    detail = client.get(f"/measurements/{sesi.id}").json()

    # Assert
    test = next(kasus for kasus in detail["cases"] if kasus["masked"])
    assert test["attempts"][0]["id"] == str(gagal_test.id)
    assert test["attempts"][0]["answer_text"] is None
    calls = client.get(_calls(sesi.id, test["attempts"][0]["id"]))
    assert calls.status_code == 200
    assert calls.json()["masked"] is True


def test_id_percobaan_tercatat_di_kontrak(client):
    """Positif: FE membangkitkan tipe dari OpenAPI, jadi id wajib ada dan bertipe UUID."""
    # Act
    skema = client.get("/openapi.json").json()["components"]["schemas"]["AttemptResult"]

    # Assert
    assert "id" in skema["required"]
    assert skema["properties"]["id"]["format"] == "uuid"
