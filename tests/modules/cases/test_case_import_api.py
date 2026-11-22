"""GET /case-imports/template, POST /case-imports, GET /case-imports/{id}.

Baris valid tersimpan walau baris lain gagal, baris gagal dilaporkan dengan alasan, baris kembar
dilewati. Kasus hasil impor adalah draft baru dengan kode dari sistem, ditulis pengimpor, di satu
suite sementara per berkas. Kembar = sidik jari judul + pertanyaan, dicek ke kasus yang ada, ke
baris lain di berkas, dan ke baris impor sebelumnya.
"""

import csv
import io
import json
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.cases import import_service
from app.modules.cases.csv_import import TEMPLATE_COLUMNS, template_csv
from app.modules.cases.models import (
    Case,
    CaseOrigin,
    CaseStatus,
    ImportBatch,
    ImportRowResult,
    ImportRowStatus,
)
from app.modules.suites.models import Suite
from app.shared.security import Role
from tests.legal_refs import rujukan_pasal
from tests.modules.cases.test_cases import _badan, _buat, _suite
from tests.modules.conftest import USER_ID_QA


@pytest.fixture
def pengimpor(as_role):
    return as_role(Role.CONTRIBUTOR)


def _baris(**isi: str) -> list[str]:
    return [isi.get(kolom, "") for kolom in TEMPLATE_COLUMNS]


def _berkas(*baris: list[str]) -> bytes:
    keluaran = io.StringIO()
    penulis = csv.writer(keluaran, lineterminator="\r\n")
    penulis.writerow(TEMPLATE_COLUMNS)
    penulis.writerows(baris)
    return keluaran.getvalue().encode("utf-8")


def _impor(client, data: bytes, nama: str = "kasus-lama.csv"):
    return client.post("/case-imports", files={"file": (nama, data, "text/csv")})


def _lengkap(
    judul: str = "PHK sepihak", pertanyaan: str = "Apakah PHK tanpa surat sah?"
) -> list[str]:
    return _baris(
        title=judul,
        question=pertanyaan,
        category="ketenagakerjaan",
        split_tag="test",
        legal_refs=json.dumps(["Pasal 1 KUHPerdata", rujukan_pasal("151")]),
        must_contain='["surat"]',
        expected_conclusion="tidak sah",
    )


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.ADMIN])
def test_template_bisa_diunduh_kontributor_dan_admin(as_role, peran):
    """Template CSV dari sistem."""
    respons = as_role(peran).get("/case-imports/template")

    assert respons.status_code == 200
    assert respons.headers["content-type"].startswith("text/csv")
    assert (
        respons.headers["content-disposition"] == 'attachment; filename="template-impor-kasus.csv"'
    )
    assert respons.content == template_csv()


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI])
def test_template_dan_impor_tertutup_untuk_selain_penulis(as_role, peran):
    """Hanya Kontributor dan Admin yang menulis kasus."""
    client = as_role(peran)

    assert client.get("/case-imports/template").status_code == 403
    assert _impor(client, _berkas(_lengkap())).status_code == 403


def test_baris_valid_tersimpan_baris_gagal_dilaporkan(pengimpor, db_session):
    """Ringkasan berhasil dan gagal beserta alasan; baris valid tetap tersimpan
    walau baris lain gagal."""
    respons = _impor(
        pengimpor,
        _berkas(
            _lengkap(), _baris(title="Rusak", must_contain="[surat"), _baris(title="Hanya judul")
        ),
    )

    assert respons.status_code == 201, respons.text
    ringkasan = respons.json()
    assert (ringkasan["total_rows"], ringkasan["success_count"], ringkasan["failed_count"]) == (
        3,
        2,
        1,
    )
    assert ringkasan["skipped_count"] == 0
    assert ringkasan["file_name"] == "kasus-lama.csv"
    assert [(b["row_no"], b["status"]) for b in ringkasan["rows"]] == [
        (2, "created"),
        (3, "failed"),
        (4, "created"),
    ]
    gagal = ringkasan["rows"][1]
    assert (gagal["case_id"], gagal["case_code"]) == (None, None)
    assert gagal["reason"].startswith("must_contain: bukan list JSON yang valid.")
    assert db_session.query(Case).count() == 2


def test_kasus_hasil_impor_adalah_draft_baru_milik_pengimpor(pengimpor, db_session):
    """Kode dari sistem, draft versi 1, origin import, penulis pengimpor,
    masuk suite sementara batch itu."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap())).json()

    baris = ringkasan["rows"][0]
    kasus = db_session.get(Case, uuid.UUID(baris["case_id"]))
    batch_hex = uuid.UUID(ringkasan["batch_id"]).hex[:8].upper()
    assert baris["case_code"] == kasus.case_code == f"IMPOR-{batch_hex}-002"
    assert (kasus.origin, kasus.import_batch_id) == (
        CaseOrigin.IMPORT,
        uuid.UUID(ringkasan["batch_id"]),
    )
    assert kasus.suite_id == uuid.UUID(ringkasan["suite_id"])
    assert (kasus.created_by, kasus.current_version.created_by) == (USER_ID_QA, USER_ID_QA)
    versi = kasus.current_version
    assert (versi.version_no, versi.status, versi.split_tag.value) == (1, CaseStatus.DRAFT, "test")
    assert kasus.latest_approved_version_id is None


def test_isi_kasus_bisa_dibaca_seperti_kasus_manual(pengimpor):
    """Rujukan campuran tersimpan dan dibaca lewat API kasus biasa."""
    case_id = _impor(pengimpor, _berkas(_lengkap())).json()["rows"][0]["case_id"]

    kasus = pengimpor.get(f"/cases/{case_id}").json()

    assert kasus["identity"] == {
        "title": "PHK sepihak",
        "question": "Apakah PHK tanpa surat sah?",
        "category": "ketenagakerjaan",
    }
    assert [rujukan["kind"] for rujukan in kasus["legal_refs"]] == ["free_text", "structured"]
    assert kasus["answer_criteria"]["must_contain"] == ["surat"]
    assert (kasus["origin"], kasus["status"], kasus["completeness_pct"]) == ("import", "draft", 100)


def test_kasus_dengan_field_kosong_tetap_bisa_dibaca_dan_dilengkapi(pengimpor):
    """Kasus lama dengan field lebih sedikit masuk sebagai draft tanpa tag;
    halaman kasus tetap bisa membukanya walau judul atau pertanyaannya kosong."""
    ringkasan = _impor(pengimpor, _berkas(_baris(question="Hanya pertanyaan"))).json()
    case_id = ringkasan["rows"][0]["case_id"]

    kasus = pengimpor.get(f"/cases/{case_id}")

    assert kasus.status_code == 200, kasus.text
    isi = kasus.json()
    assert (isi["identity"]["title"], isi["identity"]["question"]) == ("", "Hanya pertanyaan")
    assert (isi["split_tag"], isi["legal_refs"]) == (None, [])
    assert isi["completeness_pct"] < 100


def test_suite_sementara_satu_per_berkas(pengimpor, db_session):
    """Semua kasus satu berkas masuk satu suite sementara yang aktif."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap("A", "1"), _lengkap("B", "2"))).json()

    suite = db_session.get(Suite, uuid.UUID(ringkasan["suite_id"]))
    assert suite.status == "active"
    assert suite.name == ringkasan["suite_name"]
    assert suite.name.startswith("Impor CSV ")
    assert "kasus-lama.csv" in suite.description
    assert {b["case_id"] for b in ringkasan["rows"]} == {
        str(k.id) for k in db_session.query(Case).filter(Case.suite_id == suite.id)
    }


def test_batch_dan_hasil_per_baris_tersimpan(pengimpor, db_session):
    """Jumlah di import_batches sama dengan hitungan import_row_results."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap(), _baris(title="x" * 301))).json()

    batch = db_session.get(ImportBatch, uuid.UUID(ringkasan["batch_id"]))
    assert (batch.total_rows, batch.success_count, batch.failed_count, batch.skipped_count) == (
        2,
        1,
        1,
        0,
    )
    assert (batch.imported_by, batch.file_name) == (USER_ID_QA, "kasus-lama.csv")
    hasil = (
        db_session.query(ImportRowResult)
        .filter_by(batch_id=batch.id)
        .order_by(ImportRowResult.row_no)
        .all()
    )
    assert [(h.row_no, h.status) for h in hasil] == [
        (2, ImportRowStatus.CREATED),
        (3, ImportRowStatus.FAILED),
    ]
    assert hasil[0].row_fingerprint == db_session.get(Case, hasil[0].case_id).content_fingerprint
    assert hasil[1].reason == "title: maksimal 300 karakter (sekarang 301)."


def test_baris_kosong_tidak_dicatat(pengimpor):
    """Baris yang seluruhnya kosong dilewati tanpa dicatat gagal."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap(), _baris(), _lengkap("B", "2"))).json()

    assert ringkasan["total_rows"] == 2
    assert [b["row_no"] for b in ringkasan["rows"]] == [2, 4]


def test_kembar_dengan_kasus_yang_sudah_ada_dilewati(pengimpor, db_session):
    """Judul + pertanyaan yang dinormalkan sama dengan kasus yang ada."""
    _buat(pengimpor, _suite(pengimpor), case_code="PDT-001")

    ringkasan = _impor(
        pengimpor, _berkas(_lengkap(" phk  SEPIHAK ", "apakah phk tanpa surat sah?"))
    ).json()

    assert (ringkasan["success_count"], ringkasan["skipped_count"]) == (0, 1)
    baris = ringkasan["rows"][0]
    assert (baris["status"], baris["case_id"]) == ("skipped", None)
    assert baris["reason"] == "Kembar dengan kasus PDT-001 yang sudah ada."
    assert db_session.query(Case).count() == 1


def test_kembar_di_dalam_berkas_yang_sama_dilewati(pengimpor):
    """Baris kedua dengan judul dan pertanyaan sama dilewati."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap(), _lengkap())).json()

    assert [b["status"] for b in ringkasan["rows"]] == ["created", "skipped"]
    assert ringkasan["rows"][1]["reason"] == "Kembar dengan baris 2 di berkas ini."


def test_impor_ulang_berkas_yang_sama_melewati_semua_baris(pengimpor, db_session):
    """Berkas yang sama diimpor lagi, semua baris dilewati."""
    data = _berkas(_lengkap("A", "1"), _lengkap("B", "2"))
    pertama = _impor(pengimpor, data).json()

    kedua = _impor(pengimpor, data).json()

    assert (kedua["success_count"], kedua["skipped_count"]) == (0, 2)
    kode = {b["case_code"] for b in pertama["rows"]}
    assert all(any(k in b["reason"] for k in kode) for b in kedua["rows"])
    assert db_session.query(Case).count() == 2


def test_baris_gagal_tidak_dianggap_ada_untuk_cek_kembar(pengimpor):
    """Baris yang gagal tidak tersimpan, jadi baris kembarannya yang valid tetap masuk."""
    rusak = _lengkap()
    rusak[TEMPLATE_COLUMNS.index("split_tag")] = "salah"

    ringkasan = _impor(pengimpor, _berkas(rusak, _lengkap())).json()

    assert [b["status"] for b in ringkasan["rows"]] == ["failed", "created"]


def test_tanpa_judul_dan_pertanyaan_dengan_isi_lain_berbeda_bukan_kembar(pengimpor):
    """Tanpa judul dan pertanyaan, baris dengan isi lain yang berbeda tetap dua kasus."""
    ringkasan = _impor(pengimpor, _berkas(_baris(category="a"), _baris(category="b"))).json()

    assert [b["status"] for b in ringkasan["rows"]] == ["created", "created"]


def test_berkas_ditolak_tidak_menyimpan_apa_pun(pengimpor, db_session):
    """Header salah = tolak seluruh berkas; tidak ada batch, suite, atau kasus."""
    respons = pengimpor.post(
        "/case-imports", files={"file": ("kasus.csv", b"judul,pertanyaan\r\nA,B\r\n", "text/csv")}
    )

    assert respons.status_code == 422
    assert respons.json()["code"] == "IMPORT_HEADER_MISMATCH"
    assert respons.json()["expected"] == list(TEMPLATE_COLUMNS)
    assert (db_session.query(ImportBatch).count(), db_session.query(Suite).count()) == (0, 0)
    assert db_session.query(Case).count() == 0


def test_tanpa_berkas_ditolak(pengimpor):
    """Field file wajib."""
    respons = pengimpor.post("/case-imports", data={"lain": "x"})

    assert respons.status_code == 422
    assert respons.json()["code"] == "IMPORT_FILE_REQUIRED"


def test_berkas_terlalu_besar_ditolak_dari_content_length(pengimpor, monkeypatch, db_session):
    """Unggahan raksasa ditolak sebelum body dibaca utuh."""
    monkeypatch.setattr(import_service, "upload_limit_bytes", lambda: 10)

    respons = _impor(pengimpor, _berkas(*[_lengkap(f"K{i}", "Q") for i in range(2000)]))

    assert respons.status_code == 422
    assert respons.json()["code"] == "IMPORT_FILE_TOO_LARGE"
    assert db_session.query(ImportBatch).count() == 0


def test_bentrok_kode_saat_simpan_hanya_menggagalkan_baris_itu(pengimpor, db_session, monkeypatch):
    """Kode yang tadi bebas dipakai penyimpanan lain; baris itu gagal dengan alasan, baris
    lain tetap tersimpan, dan impor ulang akan melewati baris yang sudah masuk."""
    kode_asli = import_service.free_case_code
    dipakai = _buat(pengimpor, _suite(pengimpor), case_code="PDT-009").json()["case_code"]
    panggilan = iter([dipakai])
    monkeypatch.setattr(
        import_service,
        "free_case_code",
        lambda db, dasar: next(panggilan, None) or kode_asli(db, dasar),
    )

    ringkasan = _impor(pengimpor, _berkas(_lengkap("A", "1"), _lengkap("B", "2"))).json()

    assert [b["status"] for b in ringkasan["rows"]] == ["failed", "created"]
    assert "Impor ulang" in ringkasan["rows"][0]["reason"]
    assert db_session.query(ImportBatch).one().failed_count == 1


def test_galat_simpan_lain_tidak_disembunyikan(pengimpor, monkeypatch):
    """IntegrityError selain bentrok kode tetap dilempar, bukan dicatat diam-diam."""

    def gagal(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    monkeypatch.setattr(import_service, "_simpan_kasus", gagal)

    with pytest.raises(IntegrityError):
        _impor(pengimpor, _berkas(_lengkap()))


def test_ringkasan_bisa_dibuka_lagi(pengimpor):
    """Ringkasan yang sama bisa dibaca ulang dari batch-nya."""
    ringkasan = _impor(pengimpor, _berkas(_lengkap(), _baris(split_tag="x"))).json()

    lagi = pengimpor.get(f"/case-imports/{ringkasan['batch_id']}")

    assert lagi.status_code == 200
    assert lagi.json() == ringkasan


def test_ringkasan_batch_tidak_dikenal_404(pengimpor):
    """Batch yang tidak ada."""
    respons = pengimpor.get(f"/case-imports/{uuid.uuid4()}")

    assert respons.status_code == 404
    assert respons.json()["code"] == "IMPORT_BATCH_NOT_FOUND"


def test_impor_ulang_setelah_kasus_disunting_tetap_dilewati(pengimpor, db_session):
    """Kasus hasil impor yang judulnya sudah diubah tidak dibuat lagi saat berkas
    yang sama diimpor ulang; sidik jari baris impor sebelumnya ikut dicek."""
    data = _berkas(_lengkap())
    pertama = _impor(pengimpor, data).json()["rows"][0]
    disunting = pengimpor.put(
        f"/cases/{pertama['case_id']}",
        json=_badan(case_code=pertama["case_code"], split_tag="test")
        | {"identity": {"title": "PHK tanpa surat", "question": "Apakah sah?"}},
    )
    assert disunting.status_code == 200, disunting.text

    ulang = _impor(pengimpor, data).json()

    baris = ulang["rows"][0]
    assert (baris["status"], baris["case_id"]) == ("skipped", None)
    assert baris["reason"] == (
        f"Kembar dengan baris 2 dari impor sebelumnya (kasus {pertama['case_code']})."
    )
    assert db_session.query(Case).count() == 1


def test_suite_sementara_tanpa_kasus_diarsipkan(pengimpor, db_session):
    """Semua baris gagal atau dilewati, jadi suite sementaranya diarsipkan supaya tidak
    menumpuk suite kosong yang aktif; ringkasannya tetap bisa dibuka."""
    ringkasan = _impor(pengimpor, _berkas(_baris(split_tag="salah"))).json()

    suite = db_session.get(Suite, uuid.UUID(ringkasan["suite_id"]))
    assert (ringkasan["success_count"], suite.status) == (0, "archived")
    assert pengimpor.get(f"/case-imports/{ringkasan['batch_id']}").status_code == 200


def test_jumlah_batch_tetap_benar_bila_impor_terhenti(pengimpor, db_session, monkeypatch):
    """Galat tak terduga di baris kedua; baris pertama sudah tersimpan dan jumlah di batch
    sudah ikut dihitung, bukan menunggu akhir impor."""
    kode_asli = import_service.free_case_code
    panggilan = iter([None, RuntimeError("putus")])

    def kode(db, dasar):
        galat = next(panggilan)
        if galat is not None:
            raise galat
        return kode_asli(db, dasar)

    monkeypatch.setattr(import_service, "free_case_code", kode)

    with pytest.raises(RuntimeError):
        _impor(pengimpor, _berkas(_lengkap("A", "1"), _lengkap("B", "2")))

    db_session.expire_all()
    batch = db_session.query(ImportBatch).one()
    assert (batch.total_rows, batch.success_count, batch.failed_count) == (2, 1, 0)
    assert db_session.query(ImportRowResult).count() == 1


def test_kasus_hasil_impor_lewat_review_dua_reviewer(pengimpor, db_session):
    """Setelah dilengkapi, kasus hasil impor diajukan seperti kasus manual: dua
    reviewer ditugaskan dan pengimpor (penulisnya) tidak pernah menjadi reviewer."""
    for nomor in range(1, 4):
        db_session.add(
            User(
                id=uuid.UUID(f"00000000-0000-0000-0000-{nomor:012d}"),
                email=f"reviewer{nomor}@veritask.test",
                name=f"Reviewer {nomor}",
                role=Role.CONTRIBUTOR,
                is_active=True,
            )
        )
    db_session.add(
        User(id=USER_ID_QA, email="qa@veritask.test", name="Pengimpor", role=Role.CONTRIBUTOR)
    )
    db_session.commit()
    baris = _impor(pengimpor, _berkas(_baris(title="Belum lengkap"))).json()["rows"][0]
    belum = pengimpor.post(f"/cases/{baris['case_id']}/submit-review")
    assert (belum.status_code, belum.json()["code"]) == (422, "CASE_NOT_READY")
    pengimpor.put(f"/cases/{baris['case_id']}", json=_badan(case_code=baris["case_code"]))

    diajukan = pengimpor.post(f"/cases/{baris['case_id']}/submit-review")

    assert diajukan.status_code == 200, diajukan.text
    review = pengimpor.get(f"/cases/{baris['case_id']}/review").json()
    reviewer = [slot["reviewer"]["id"] for slot in review["reviewers"]]
    assert len(reviewer) == 2
    assert str(USER_ID_QA) not in reviewer


def test_impor_ulang_baris_tanpa_judul_dan_pertanyaan_dilewati(pengimpor, db_session):
    """Berkas yang sama diimpor lagi; baris yang hanya berisi kategori tidak
    punya sidik jari isi, jadi dikenali dari identitas baris (semua kolomnya)."""
    data = _berkas(_baris(category="ketenagakerjaan"))
    pertama = _impor(pengimpor, data).json()["rows"][0]

    ulang = _impor(pengimpor, data).json()["rows"][0]

    assert (ulang["status"], ulang["case_id"]) == ("skipped", None)
    assert ulang["reason"] == (
        f"Kembar dengan baris 2 dari impor sebelumnya (kasus {pertama['case_code']})."
    )
    assert db_session.query(Case).count() == 1


def test_baris_tanpa_judul_dan_pertanyaan_yang_sama_di_berkas_dilewati(pengimpor):
    """Dua baris identik tanpa judul dan pertanyaan di satu berkas, yang kedua dilewati."""
    sama = _baris(category="ketenagakerjaan", must_contain='["surat"]')

    ringkasan = _impor(pengimpor, _berkas(sama, sama)).json()

    assert [b["status"] for b in ringkasan["rows"]] == ["created", "skipped"]
    assert ringkasan["rows"][1]["reason"] == "Kembar dengan baris 2 di berkas ini."


def test_baris_tanpa_judul_tidak_kembar_dengan_kasus_manual_hanya_karena_kosong(
    pengimpor, db_session
):
    """Identitas baris hanya dibandingkan dengan baris impor, bukan kasus manual, jadi
    draft kosong tidak pernah dianggap kembar dengan kasus yang ditulis manual."""
    _buat(pengimpor, _suite(pengimpor), case_code="PDT-002")

    baris = _impor(pengimpor, _berkas(_baris(category="ketenagakerjaan"))).json()["rows"][0]

    assert baris["status"] == "created"
