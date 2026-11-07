"""GET /measurements/{id}: detail sesi dengan jawaban mentah. PBI-11 AC8, SCRUM-210.

Detail memuat kolom yang sama dengan daftar, ditambah setiap kasus di snapshot
beserta jawaban mentah per percobaan. Sesi yang diarsipkan tetap bisa dibuka.

Developer AI tidak boleh melihat isi kasus test (SCRUM-183). Barisnya tetap ada
supaya progres cocok, tetapi kode, judul, pertanyaan, dan jawabannya disamarkan
seperti daftar kasus (masked = true).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak atau data yang tidak boleh keluar.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from app.modules.auth.models import User
from app.modules.cases.sections import snapshot_body
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    SessionStatus,
)
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

# Isi kasus sintetis untuk tes, bukan materi Veritask.
PERTANYAAN_TEST = "Pertanyaan rahasia split test"
JAWABAN_TEST = "Jawaban produk untuk kasus test"
JAWABAN_BERJALAN = "Jawaban produk yang masuk saat sesi berjalan"


def _url(session_id: uuid.UUID) -> str:
    return f"/measurements/{session_id}"


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-detail@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Detail")
    db_session.add(item)
    db_session.commit()
    return item


def _body(case_code: str, split_tag: str, pertanyaan: str) -> dict:
    return snapshot_body(
        case_code=case_code,
        version_no=1,
        status="approved",
        split_tag=split_tag,
        content={
            "title": f"Judul {case_code}",
            "question": pertanyaan,
            "category": "kategori-uji",
            "answer_criteria": {"catatan": "kriteria sintetis"},
            "traps": [{"catatan": "jebakan sintetis"}],
        },
        legal_refs=[],
    )


def _snapshot(db_session, sesi) -> CaseSnapshot:
    snapshot = CaseSnapshot(session_id=sesi.id)
    db_session.add(snapshot)
    db_session.flush()
    return snapshot


def _kasus(db_session, sesi, snapshot, body: dict, percobaan: list[tuple]) -> CaseSnapshotItem:
    """Satu kasus di snapshot. percobaan: (status, jawaban) per nomor pengulangan."""
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id, case_id=uuid.uuid4(), case_version_id=uuid.uuid4(), body=body
    )
    db_session.add(item)
    db_session.flush()
    for nomor, (status, jawaban) in enumerate(percobaan, 1):
        db_session.add(
            Attempt(
                session_id=sesi.id,
                snapshot_item_id=item.id,
                repetition_no=nomor,
                source=AttemptSource.AUTOMATIC,
                status=status,
                answer_text=jawaban,
                model_version="model-uji-2026" if jawaban else None,
            )
        )
    db_session.commit()
    return item


@pytest.fixture
def sesi_campuran(db_session, produk, pembuat):
    """Sesi selesai dengan satu kasus dev dan satu kasus test, dua pengulangan."""
    sesi = _sesi(db_session, produk, pembuat, label="Run Detail", status=SessionStatus.FINISHED)
    snapshot = _snapshot(db_session, sesi)
    _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-002", "test", PERTANYAAN_TEST),
        [(AttemptStatus.SUCCEEDED, JAWABAN_TEST), (AttemptStatus.SUCCEEDED, JAWABAN_TEST)],
    )
    _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-001", "dev", "Pertanyaan dev"),
        [(AttemptStatus.SUCCEEDED, "Jawaban dev 1"), (AttemptStatus.TECHNICAL_FAILURE, None)],
    )
    return sesi


def _per_kode(cases: list[dict]) -> dict:
    return {item["case_code"]: item for item in cases}


def test_admin_melihat_jawaban_mentah_per_percobaan(as_role, db_session, sesi_campuran, produk):
    """Positif: kolom daftar ikut, lalu setiap kasus dengan percobaannya berurutan."""
    # Act
    response = as_role(Role.ADMIN).get(_url(sesi_campuran.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(sesi_campuran.id)
    assert body["label"] == "Run Detail"
    assert body["product_name"] == "Produk Detail"
    assert body["created_by_name"] == "Admin Sesi"
    assert body["case_count"] == 2
    assert body["progress"] == {"completed": 4, "total": 4}
    assert body["purpose"] == "trial"
    assert body["split_filter"] == "both"
    assert [item["case_code"] for item in body["cases"]] == ["KASUS-001", "KASUS-002"]
    dev = body["cases"][0]
    assert dev["title"] == "Judul KASUS-001"
    assert dev["question"] == "Pertanyaan dev"
    assert dev["split_tag"] == "dev"
    assert dev["masked"] is False
    # id percobaan dipakai FE untuk coba ulang per kasus dan detail teknis (SCRUM-218).
    id_dev = db_session.scalars(
        select(Attempt.id)
        .where(Attempt.snapshot_item_id == uuid.UUID(dev["snapshot_item_id"]))
        .order_by(Attempt.repetition_no)
    ).all()
    assert dev["attempts"] == [
        {
            "id": str(id_dev[0]),
            "repetition_no": 1,
            "status": "succeeded",
            "answer_text": "Jawaban dev 1",
            "model_version": "model-uji-2026",
        },
        {
            "id": str(id_dev[1]),
            "repetition_no": 2,
            "status": "technical_failure",
            "answer_text": None,
            "model_version": None,
        },
    ]


def test_admin_melihat_isi_kasus_test(as_role, sesi_campuran):
    """Positif: penyamaran hanya untuk Developer AI."""
    # Act
    body = as_role(Role.ADMIN).get(_url(sesi_campuran.id)).json()

    # Assert
    test = _per_kode(body["cases"])["KASUS-002"]
    assert test["question"] == PERTANYAAN_TEST
    assert test["attempts"][0]["answer_text"] == JAWABAN_TEST


def test_developer_ai_melihat_kasus_dev_utuh(as_role, sesi_campuran):
    """Positif: kasus dev tetap terbaca oleh Developer AI."""
    # Act
    response = as_role(Role.DEVELOPER_AI).get(_url(sesi_campuran.id))

    # Assert
    assert response.status_code == 200
    dev = _per_kode(response.json()["cases"])["KASUS-001"]
    assert dev["masked"] is False
    assert dev["attempts"][0]["answer_text"] == "Jawaban dev 1"


def test_developer_ai_tidak_melihat_isi_kasus_test(as_role, sesi_campuran):
    """Negatif: kode, judul, pertanyaan, dan jawaban kasus test tidak keluar sama sekali."""
    # Act
    response = as_role(Role.DEVELOPER_AI).get(_url(sesi_campuran.id))

    # Assert
    assert PERTANYAAN_TEST not in response.text
    assert JAWABAN_TEST not in response.text
    assert "KASUS-002" not in response.text
    test = _per_kode(response.json()["cases"])["TERSEMBUNYI"]
    assert test["masked"] is True
    assert test["title"] == "Kasus test disembunyikan"
    assert test["question"] is None
    assert [a["status"] for a in test["attempts"]] == ["succeeded", "succeeded"]
    assert {a["answer_text"] for a in test["attempts"]} == {None}
    assert test["version_no"] is None
    assert test["sections"] is None


def test_detail_memuat_semua_bagian_snapshot(as_role, sesi_campuran):
    """Positif: delapan bagian snapshot dan nomor versinya ikut, seperti compare versi (AC7)."""
    # Act
    body = as_role(Role.ADMIN).get(_url(sesi_campuran.id)).json()

    # Assert
    dev = _per_kode(body["cases"])["KASUS-001"]
    assert dev["version_no"] == 1
    assert dev["sections"] == {
        "identity.title": "Judul KASUS-001",
        "identity.question": "Pertanyaan dev",
        "category": "kategori-uji",
        "case_code": "KASUS-001",
        "split_tag": "dev",
        "legal_refs": [],
        "answer_criteria": {"catatan": "kriteria sintetis"},
    }


def test_detail_snapshot_lama_berisi_traps_tetap_terbaca_tanpa_traps(
    as_role, db_session, produk, pembuat
):
    """Edge: snapshot dari sebelum SCRUM-256 masih punya sections["traps"], respons membuangnya."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    snapshot = _snapshot(db_session, sesi)
    body = _body("KASUS-LAMA", "dev", "Pertanyaan snapshot lama")
    body["sections"]["traps"] = [{"description": "jebakan lama sintetis"}]
    _kasus(db_session, sesi, snapshot, body, [(AttemptStatus.SUCCEEDED, "Jawaban lama")])

    # Act
    response = as_role(Role.ADMIN).get(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    lama = _per_kode(response.json()["cases"])["KASUS-LAMA"]
    assert "traps" not in lama["sections"]
    assert lama["sections"]["identity.question"] == "Pertanyaan snapshot lama"
    assert "jebakan lama sintetis" not in response.text


def test_detail_memuat_waktu_snapshot(as_role, sesi_campuran):
    """Positif: waktu snapshot dibuat ditampilkan di halaman detail (AC6)."""
    # Act
    body = as_role(Role.ADMIN).get(_url(sesi_campuran.id)).json()

    # Assert
    assert body["snapshot_created_at"] is not None


def _sesi_berjalan(db_session, produk, pembuat):
    """Sesi berjalan dengan satu kasus dev yang percobaan pertamanya sudah dijawab."""
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    snapshot = _snapshot(db_session, sesi)
    _kasus(
        db_session,
        sesi,
        snapshot,
        _body("KASUS-003", "dev", "Pertanyaan dev berjalan"),
        [(AttemptStatus.SUCCEEDED, JAWABAN_BERJALAN), (AttemptStatus.PENDING, None)],
    )
    return sesi


def test_developer_ai_melihat_jawaban_kasus_dev_saat_sesi_berjalan(
    as_role, db_session, produk, pembuat
):
    """Positif: AC9 jawaban kasus dev terbaca begitu percobaannya selesai, sesi masih berjalan."""
    # Arrange
    sesi = _sesi_berjalan(db_session, produk, pembuat)

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["progress"] == {"completed": 1, "total": 2}
    dev = body["cases"][0]
    assert dev["masked"] is False
    assert [a["status"] for a in dev["attempts"]] == ["succeeded", "pending"]
    assert [a["answer_text"] for a in dev["attempts"]] == [JAWABAN_BERJALAN, None]


def test_developer_ai_tidak_melihat_kasus_test_saat_sesi_berjalan(
    as_role, db_session, produk, pembuat
):
    """Negatif: kasus test tetap tertutup selama sesi berjalan, jawaban dev di sebelahnya terbuka."""
    # Arrange
    sesi = _sesi_berjalan(db_session, produk, pembuat)
    _kasus(
        db_session,
        sesi,
        db_session.scalars(select(CaseSnapshot).where(CaseSnapshot.session_id == sesi.id)).one(),
        _body("KASUS-009", "test", PERTANYAAN_TEST),
        [(AttemptStatus.SUCCEEDED, JAWABAN_TEST), (AttemptStatus.PENDING, None)],
    )

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_url(sesi.id))

    # Assert
    assert JAWABAN_BERJALAN in response.text
    assert PERTANYAAN_TEST not in response.text
    assert JAWABAN_TEST not in response.text
    assert "KASUS-009" not in response.text
    test = _per_kode(response.json()["cases"])["TERSEMBUNYI"]
    assert test["masked"] is True
    assert {a["answer_text"] for a in test["attempts"]} == {None}


@pytest.mark.parametrize("split_tag", [None, "tag-tak-dikenal"])
def test_developer_ai_tidak_melihat_kasus_bertag_kosong_atau_tak_dikenal(
    as_role, db_session, produk, pembuat, split_tag
):
    """Edge: fail-closed. Kasus yang tidak jelas dev atau test diperlakukan seperti test."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    body = _body("KASUS-007", "dev", PERTANYAAN_TEST)
    body["sections"]["split_tag"] = split_tag
    _kasus(
        db_session,
        sesi,
        _snapshot(db_session, sesi),
        body,
        [(AttemptStatus.SUCCEEDED, JAWABAN_TEST)],
    )

    # Act
    response = as_role(Role.DEVELOPER_AI).get(_url(sesi.id))

    # Assert
    assert PERTANYAAN_TEST not in response.text
    assert JAWABAN_TEST not in response.text
    assert "KASUS-007" not in response.text
    kasus = response.json()["cases"][0]
    assert kasus["masked"] is True
    assert kasus["case_code"] == "TERSEMBUNYI"


def test_admin_melihat_jawaban_sesi_berjalan(as_role, db_session, produk, pembuat):
    """Positif: Admin memantau jawaban yang masuk selama sesi berjalan."""
    # Arrange
    sesi = _sesi_berjalan(db_session, produk, pembuat)

    # Act
    body = as_role(Role.ADMIN).get(_url(sesi.id)).json()

    # Assert
    assert body["cases"][0]["attempts"][0]["answer_text"] == JAWABAN_BERJALAN


def test_status_sesi_dan_percobaan_berupa_enum_di_kontrak(client):
    """Positif: FE mendapat tipe label status dari OpenAPI, bukan string bebas."""
    # Act
    skema = client.get("/openapi.json").json()["components"]["schemas"]

    # Assert
    assert set(skema["SessionStatus"]["enum"]) == {item.value for item in SessionStatus}
    assert set(skema["AttemptStatus"]["enum"]) == {item.value for item in AttemptStatus}
    assert skema["SessionListItem"]["properties"]["status"] == {
        "$ref": "#/components/schemas/SessionStatus"
    }
    assert skema["AttemptResult"]["properties"]["status"] == {
        "$ref": "#/components/schemas/AttemptStatus"
    }


def test_tujuan_filter_dan_split_berupa_enum_di_kontrak(client):
    """Positif: tipe yang sama dengan form Mulai dan kasus, supaya FE tidak memegang string bebas."""
    # Act
    skema = client.get("/openapi.json").json()["components"]["schemas"]

    # Assert
    detail = skema["SessionDetail"]["properties"]
    assert detail["purpose"] == {"$ref": "#/components/schemas/MeasurementPurpose"}
    assert detail["split_filter"] == {"$ref": "#/components/schemas/CaseFilter"}
    split_tag = skema["SessionCaseResult"]["properties"]["split_tag"]
    assert {"$ref": "#/components/schemas/SplitTag"} in split_tag["anyOf"]


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_selain_admin_dan_developer_ai_ditolak_403(as_role, sesi_campuran, role):
    """Negatif: jawaban mentah hanya untuk peran yang menjalankan pengukuran."""
    # Act
    response = as_role(role).get(_url(sesi_campuran.id))

    # Assert
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_tanpa_login_ditolak_401(client):
    """Negatif: tanpa sesi login tidak ada jawaban yang keluar."""
    # Act
    response = client.get(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_sesi_yang_tidak_ada_ditolak_404(as_role):
    """Negatif: id sesi yang tidak dikenal."""
    # Act
    response = as_role(Role.ADMIN).get(_url(uuid.uuid4()))

    # Assert
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_sesi_antre_belum_punya_kasus(as_role, db_session, produk, pembuat):
    """Edge: snapshot dibuat saat mulai, jadi detail sesi antre belum berisi kasus."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)

    # Act
    body = as_role(Role.ADMIN).get(_url(sesi.id)).json()

    # Assert
    assert body["status"] == "queued"
    assert body["case_count"] is None
    assert body["cases"] == []
    assert body["snapshot_created_at"] is None


def test_sesi_yang_diarsipkan_tetap_bisa_dibuka(as_role, db_session, produk, pembuat):
    """Edge: arsip hanya menyembunyikan dari daftar, hasilnya tetap bisa dibaca (AC10)."""
    # Arrange
    waktu = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    sesi = _sesi(
        db_session,
        produk,
        pembuat,
        status=SessionStatus.FINISHED,
        archived_at=waktu,
        archived_by=pembuat.id,
    )

    # Act
    response = as_role(Role.ADMIN).get(_url(sesi.id))

    # Assert
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["archived_at"]).replace(tzinfo=UTC) == waktu


def _jumlah_query(db_session, client, session_id) -> int:
    query: list[str] = []

    def catat(_conn, _cursor, statement, *_args):
        query.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", catat)
    try:
        assert client.get(_url(session_id)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", catat)
    return len(query)


def test_jumlah_query_tidak_bertambah_per_kasus(as_role, db_session, produk, pembuat):
    """Edge: satu kasus atau lima kasus, jumlah query sama (tanpa N+1)."""
    # Arrange
    client = as_role(Role.ADMIN)
    sedikit = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    snapshot = _snapshot(db_session, sedikit)
    _kasus(db_session, sedikit, snapshot, _body("A-1", "dev", "q"), [(AttemptStatus.PENDING, None)])
    banyak = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    snapshot = _snapshot(db_session, banyak)
    for nomor in range(5):
        _kasus(
            db_session,
            banyak,
            snapshot,
            _body(f"B-{nomor}", "dev", "q"),
            [(AttemptStatus.PENDING, None)],
        )

    # Act
    satu_kasus = _jumlah_query(db_session, client, sedikit.id)
    lima_kasus = _jumlah_query(db_session, client, banyak.id)

    # Assert
    assert lima_kasus == satu_kasus
