"""QA Laporan Perbandingan Kualitas AI Hukum antar Produk. PBI-15 SCRUM-79 SCRUM-323.

Tujuan: laporan perbandingan terbukti benar, hanya memakai sesi yang layak,
dan skor dev tidak bocor ke peran yang tidak berhak.

Label pengujian:
- Positif: laporan dari sesi layak menampilkan tabel, batang, peringkat, versi, dan jumlah
  kasus (AC1, AC3, AC7, AC13, AC14). Laporan tersimpan dibuka ulang dengan angka sama (AC16).
  Dev AI dan Admin dapat membuat laporan dev (AC18, AC20).
- Negatif: sesi dibatalkan atau run gagal tanpa "terima apa adanya" ditolak (AC2, AC4). Sesi
  belum dinilai atau manual belum Selesai ditolak (D2). Versi rubrik, model penilai, atau
  suite berbeda ditolak dengan alasan (AC19). Viewer atau Kontributor meminta skor dev lewat
  API mentah -> 403 (AC12, AC21). Laporan dev diterbitkan -> ditolak (D1, D9).
- Edge: irisan kasus kosong -> peringatan tanpa angka (AC7). Produk manual tanpa tingkat
  kegagalan (AC2). Percobaan manual berbeda atau rentang tanggal > 7 hari -> penanda (AC10).
  Versi model berbeda -> penanda (AC3). Nilai ulang sesi tidak mengubah laporan tersimpan (AC16).
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    DUPLICATE_PRODUCT,
    JUDGE_MODEL,
    NEED_AT_LEAST_TWO_PRODUCTS,
    NO_SHARED_CASES,
    RUBRIC_VERSION,
    SESSION_CANCELLED,
    SESSION_NOT_ACCEPTED_AS_IS,
    SESSION_NOT_FINISHED,
    SESSION_NOT_GRADED,
    SESSION_RUN_FAILED,
    SUITE_VERSION,
    CaseRef,
    SessionFacts,
)
from app.modules.reports.creation_policy import REPORT_LABEL_MODE_FORBIDDEN, REPORT_SCOPE_FORBIDDEN
from app.modules.reports.dependencies import get_session_record_reader
from app.modules.reports.models import (
    MeasurementKind,
    ReportMarkerCode,
    ScoreSplit,
)
from app.modules.reports.report_generation import REPORT_NOT_COMPARABLE
from app.modules.reports.report_scoring import CallCounts, GradedCase
from app.modules.reports.service import REPORT_HAS_DEV_SCORES
from app.modules.reports.session_record import SessionRecord
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType, SplitFilter
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu

URL_REPORTS = "/reports"

RUBRIK_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
RUBRIK_B = uuid.UUID("11111111-1111-1111-1111-111111111112")
PENILAI_A = uuid.UUID("22222222-2222-2222-2222-222222222221")
PENILAI_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
SUITE_A = uuid.UUID("33333333-3333-3333-3333-333333333331")
SUITE_B = uuid.UUID("33333333-3333-3333-3333-333333333332")
AWAL = datetime(2026, 11, 1, tzinfo=UTC)


def _ref(nomor: int, *, versi: int = 1, split: SplitTag = SplitTag.TEST) -> CaseRef:
    return CaseRef(
        case_id=uuid.UUID(f"{nomor:032x}"),
        case_version_id=uuid.UUID(f"{nomor:016x}{versi:016x}"),
        case_code=f"CASE-{nomor:03d}",
        split_tag=split.value,
    )


def _nilai(nomor: int, det: str | None, llm: str | None, split: SplitTag = SplitTag.TEST):
    return GradedCase(
        ref=_ref(nomor, split=split),
        split=split,
        det=None if det is None else Decimal(det),
        llm=None if llm is None else Decimal(llm),
    )


def _rekaman(nama: str, graded: list[GradedCase], **ubah) -> SessionRecord:
    tipe = ubah.pop("session_type", SessionType.AUTOMATIC)
    kasus_snapshot = tuple(g.ref for g in graded) + tuple(ubah.pop("snapshot_tambahan", ()))
    facts = SessionFacts(
        session_id=ubah.pop("session_id", uuid.uuid4()),
        product_id=ubah.pop("product_id", uuid.uuid4()),
        product_name=nama,
        session_type=tipe,
        purpose=ubah.pop("purpose", SessionPurpose.OFFICIAL),
        status=ubah.pop("status", SessionStatus.FINISHED),
        accepted_as_is_at=ubah.pop("accepted_as_is_at", None),
        rubric_version_id=ubah.pop("rubrik", RUBRIK_A),
        judge_provider_id=PENILAI_A,
        judge_model_version=ubah.pop("judge_versi", "judge-v1"),
        suite_ids=ubah.pop("suite_ids", frozenset({SUITE_A})),
        model_version=ubah.pop("model", "v1.0"),
        repetitions=ubah.pop("repetisi", 1),
        answer_date_from=ubah.pop("dari", None),
        answer_date_to=ubah.pop("sampai", None),
        scored_cases=tuple(g.ref for g in graded),
        snapshot_cases=kasus_snapshot,
        split_filter=ubah.pop("split_filter", SplitFilter.BOTH),
    )
    return SessionRecord(
        facts=facts,
        graded_cases=tuple(graded),
        calls_by_split=ubah.pop("calls", {}),
        label=ubah.pop("label", f"Sesi {nama}"),
        created_at=ubah.pop("dibuat", AWAL),
    )


@pytest.fixture
def pasang_pembaca():
    def pasang(*rekaman):
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu(*rekaman)

    yield pasang
    app.dependency_overrides.pop(get_session_record_reader, None)


def _isi_request(rekaman_list, **ubah) -> dict:
    return {
        "title": "Laporan Uji QA PBI-15",
        "session_ids": [str(r.facts.session_id) for r in rekaman_list],
        "score_scope": "test",
    } | ubah


# ==============================================================================
# 1. PENGUJIAN POSITIF (AC1, AC3, AC7, AC9, AC13, AC14, AC16, AC18, AC20)
# ==============================================================================


class TestQAPositif:
    def test_laporan_dari_sesi_layak_menampilkan_tabel_batang_peringkat_dan_jumlah_kasus(
        self, as_role, pasang_pembaca
    ):
        """Positif: AC1, AC3, AC7, AC13, AC14. Laporan dari sesi layak menampilkan tabel,

        batang, peringkat, versi, dan jumlah kasus.
        """
        # Arrange: 3 produk (DeepSeek, Gemini, AiYU) pada snapshot suite yang sama (kasus 1-4)
        # DeepSeek menjawab 1, 2, 3; Gemini menjawab 1, 2; AiYU menjawab 1, 2, 4.
        p1 = _rekaman(
            "DeepSeek",
            [_nilai(1, "0.90", "0.80"), _nilai(2, "0.80", "0.80"), _nilai(3, "0.70", "0.70")],
            snapshot_tambahan=(_ref(4),),
            model="deepseek-chat-v3",
            judge_versi="judge-gpt4o",
            calls={ScoreSplit.TEST: CallCounts(total=4, failed=1)},
        )
        p2 = _rekaman(
            "Gemini",
            [_nilai(1, "0.70", "0.60"), _nilai(2, "0.60", "0.50")],
            snapshot_tambahan=(_ref(3), _ref(4)),
            model="gemini-1.5-pro",
            judge_versi="judge-gpt4o",
            calls={ScoreSplit.TEST: CallCounts(total=2, failed=0)},
        )
        p3 = _rekaman(
            "AiYU",
            [_nilai(1, "0.50", "0.40"), _nilai(2, "0.40", "0.30"), _nilai(4, "0.60", "0.60")],
            snapshot_tambahan=(_ref(3),),
            model="aiyu-legal-v1",
            judge_versi="judge-gpt4o",
            calls={ScoreSplit.TEST: CallCounts(total=3, failed=0)},
        )
        pasang_pembaca(p1, p2, p3)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([p1, p2, p3]))

        # Assert: 201 Created
        assert respons.status_code == 201
        data = respons.json()
        assert data["warning"] is None
        laporan = data["report"]

        # AC1: Irisan kasus dihitung pada kasus yang dijawab oleh semua produk (kasus 1 dan 2)
        assert laporan["case_basis_count"] == 2
        assert laporan["score_scope"] == "test"
        assert laporan["has_dev_scores"] is False
        assert laporan["status"] == "saved"
        assert str(laporan["rubric_version_id"]) == str(RUBRIK_A)
        assert laporan["judge_model_name"] == "judge-gpt4o"

        # AC1, AC7: Verifikasi hasil tiap produk berdampingan
        sesi_hasil = {s["product_label"]: s for s in laporan["sessions"]}
        assert set(sesi_hasil.keys()) == {"DeepSeek", "Gemini", "AiYU"}

        # DeepSeek
        ds = sesi_hasil["DeepSeek"]
        ds_res = ds["results"][0]
        assert ds_res["cases_basis_count"] == 2
        assert ds_res["cases_graded_count"] == 3
        assert ds_res["det_score"] == "0.85"
        assert ds_res["llm_score"] == "0.80"
        assert ds_res["total_score"] == "0.83"
        assert ds_res["failure_rate"] == "25.00"
        assert ds_res["rank"] == 1
        assert ds_res["product_model_version"] == "deepseek-chat-v3"

        # Gemini
        gm = sesi_hasil["Gemini"]
        gm_res = gm["results"][0]
        assert gm_res["cases_basis_count"] == 2
        assert gm_res["cases_graded_count"] == 2
        assert gm_res["det_score"] == "0.65"
        assert gm_res["llm_score"] == "0.55"
        assert gm_res["total_score"] == "0.60"
        assert gm_res["failure_rate"] == "0.00"
        assert gm_res["rank"] == 2

        # AiYU
        ay = sesi_hasil["AiYU"]
        ay_res = ay["results"][0]
        assert ay_res["cases_basis_count"] == 2
        assert ay_res["cases_graded_count"] == 3
        assert ay_res["det_score"] == "0.45"
        assert ay_res["llm_score"] == "0.35"
        assert ay_res["total_score"] == "0.40"
        assert ay_res["failure_rate"] == "0.00"
        assert ay_res["rank"] == 3

    def test_laporan_tersimpan_dibuka_ulang_dengan_angka_sama(self, as_role, pasang_pembaca):
        """Positif: AC16. Laporan tersimpan dibuka ulang menghasilkan angka yang persis sama."""
        # Arrange
        p1 = _rekaman("Produk A", [_nilai(1, "0.80", "0.80")])
        p2 = _rekaman("Produk B", [_nilai(1, "0.60", "0.60")])
        pasang_pembaca(p1, p2)
        admin = as_role(Role.ADMIN)

        post_resp = admin.post(URL_REPORTS, json=_isi_request([p1, p2]))
        assert post_resp.status_code == 201
        report_id = post_resp.json()["report"]["id"]

        # Act: Buka ulang lewat GET /reports/{id}
        get_resp = admin.get(f"{URL_REPORTS}/{report_id}")

        # Assert: status 200 dan data angka persis sama
        assert get_resp.status_code == 200
        data_lama = post_resp.json()["report"]
        data_buka = get_resp.json()

        assert data_buka["id"] == data_lama["id"]
        assert data_buka["title"] == data_lama["title"]
        assert data_buka["case_basis_count"] == data_lama["case_basis_count"]

        skor_lama = {
            s["product_label"]: (
                s["results"][0]["det_score"],
                s["results"][0]["llm_score"],
                s["results"][0]["rank"],
            )
            for s in data_lama["sessions"]
        }
        skor_buka = {
            s["product_label"]: (
                s["results"][0]["det_score"],
                s["results"][0]["llm_score"],
                s["results"][0]["rank"],
            )
            for s in data_buka["sessions"]
        }
        assert skor_buka == skor_lama

    def test_developer_ai_dan_admin_dapat_membuat_laporan_dev(self, as_role, pasang_pembaca):
        """Positif: AC18, AC20. Admin dan Dev AI dapat membuat laporan dev (dev atau dev_test)."""
        # Arrange
        p1 = _rekaman(
            "Produk A",
            [_nilai(1, "0.80", "0.80", SplitTag.TEST), _nilai(2, "0.90", "0.70", SplitTag.DEV)],
        )
        p2 = _rekaman(
            "Produk B",
            [_nilai(1, "0.60", "0.60", SplitTag.TEST), _nilai(2, "0.50", "0.50", SplitTag.DEV)],
        )
        pasang_pembaca(p1, p2)

        # Act 1: Admin membuat laporan dev-only
        admin = as_role(Role.ADMIN)
        resp_admin = admin.post(URL_REPORTS, json=_isi_request([p1, p2], score_scope="dev"))
        assert resp_admin.status_code == 201
        rep_dev = resp_admin.json()["report"]
        assert rep_dev["score_scope"] == "dev"
        assert rep_dev["has_dev_scores"] is True
        assert rep_dev["case_basis_count"] == 1
        assert rep_dev["sessions"][0]["results"][0]["score_split"] == "dev"

        # Act 2: Dev AI membuat laporan dev_test
        dev_ai = as_role(Role.DEVELOPER_AI)
        resp_dev_ai = dev_ai.post(URL_REPORTS, json=_isi_request([p1, p2], score_scope="dev_test"))
        assert resp_dev_ai.status_code == 201
        rep_combo = resp_dev_ai.json()["report"]
        assert rep_combo["score_scope"] == "dev_test"
        assert rep_combo["has_dev_scores"] is True
        assert rep_combo["case_basis_count"] == 2
        splits = {res["score_split"] for res in rep_combo["sessions"][0]["results"]}
        assert splits == {"test", "dev", "combined"}

    def test_admin_dapat_memilih_label_produk_mode_kode(self, as_role, pasang_pembaca):
        """Positif: AC9. Admin dapat memutuskan label produk mode kode (Product A, B, ...)."""
        # Arrange
        p1 = _rekaman("AiYU", [_nilai(1, "0.80", "0.80")])
        p2 = _rekaman("DeepSeek", [_nilai(1, "0.60", "0.60")])
        pasang_pembaca(p1, p2)
        admin = as_role(Role.ADMIN)

        # Act
        respons = admin.post(URL_REPORTS, json=_isi_request([p1, p2], product_label_mode="code"))

        # Assert
        assert respons.status_code == 201
        labels = [s["product_label"] for s in respons.json()["report"]["sessions"]]
        assert labels == ["Product A", "Product B"]


# ==============================================================================
# 2. PENGUJIAN NEGATIF (AC2, AC4, AC12, AC19, AC21, D1, D9)
# ==============================================================================


class TestQANegatif:
    def test_sesi_dibatalkan_atau_run_gagal_tanpa_terima_apa_adanya_ditolak(
        self, as_role, pasang_pembaca
    ):
        """Negatif: AC2, AC4. Sesi dibatalkan atau run gagal tanpa terima apa adanya ditolak."""
        # Arrange: sesi dibatalkan
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)
        normal = _rekaman("Normal", [_nilai(1, "0.5", "0.5")])
        pasang_pembaca(batal, normal)
        client = as_role(Role.ADMIN)

        # Act 1: Sesi CANCELLED
        resp_batal = client.post(URL_REPORTS, json=_isi_request([batal, normal]))
        assert resp_batal.status_code == 409
        body = resp_batal.json()
        assert body["code"] == REPORT_NOT_COMPARABLE
        assert SESSION_CANCELLED in body["ineligible"][str(batal.facts.session_id)]

        # Act 2: Sesi FAILED
        gagal = _rekaman("Gagal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.FAILED)
        pasang_pembaca(gagal, normal)
        resp_gagal = client.post(URL_REPORTS, json=_isi_request([gagal, normal]))
        assert resp_gagal.status_code == 409
        assert SESSION_RUN_FAILED in resp_gagal.json()["ineligible"][str(gagal.facts.session_id)]

        # Act 3: FINISHED_WITH_FAILURES tanpa terima apa adanya
        selesai_gagal = _rekaman(
            "SelesaiGagal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.FINISHED_WITH_FAILURES
        )
        pasang_pembaca(selesai_gagal, normal)
        resp_unaccepted = client.post(URL_REPORTS, json=_isi_request([selesai_gagal, normal]))
        assert resp_unaccepted.status_code == 409
        assert (
            SESSION_NOT_ACCEPTED_AS_IS
            in resp_unaccepted.json()["ineligible"][str(selesai_gagal.facts.session_id)]
        )

        # Act 4: FINISHED_WITH_FAILURES dengan terima apa adanya -> diterima (Positif complement)
        diterima = _rekaman(
            "Diterima",
            [_nilai(1, "0.5", "0.5")],
            status=SessionStatus.FINISHED_WITH_FAILURES,
            accepted_as_is_at=datetime.now(UTC),
        )
        pasang_pembaca(diterima, normal)
        resp_accepted = client.post(URL_REPORTS, json=_isi_request([diterima, normal]))
        assert resp_accepted.status_code == 201

    def test_sesi_belum_dinilai_atau_manual_belum_finalized_ditolak(self, as_role, pasang_pembaca):
        """Negatif: D2. Sesi belum dinilai (rubrik=None) atau manual belum finalized ditolak."""
        normal = _rekaman("Normal", [_nilai(1, "0.5", "0.5")])

        # Sesi belum dinilai
        belum_dinilai = _rekaman("BelumDinilai", [], rubrik=None, snapshot_tambahan=(_ref(1),))
        pasang_pembaca(belum_dinilai, normal)
        client = as_role(Role.ADMIN)
        resp_ungraded = client.post(URL_REPORTS, json=_isi_request([belum_dinilai, normal]))
        assert resp_ungraded.status_code == 409
        assert (
            SESSION_NOT_GRADED
            in resp_ungraded.json()["ineligible"][str(belum_dinilai.facts.session_id)]
        )

        # Sesi manual status COMPLETE (belum finalized)
        manual_belum = _rekaman(
            "ManualBelum",
            [_nilai(1, "0.5", "0.5")],
            session_type=SessionType.MANUAL,
            status=SessionStatus.COMPLETE,
        )
        pasang_pembaca(manual_belum, normal)
        resp_manual = client.post(URL_REPORTS, json=_isi_request([manual_belum, normal]))
        assert resp_manual.status_code == 409
        assert (
            SESSION_NOT_FINISHED
            in resp_manual.json()["ineligible"][str(manual_belum.facts.session_id)]
        )

    def test_syarat_versi_rubrik_atau_model_penilai_atau_suite_berbeda_ditolak_dengan_alasan(
        self, as_role, pasang_pembaca
    ):
        """Negatif: AC19. Syarat (a) rubrik sama, (b) penilai sama, (c) suite sama ditegakkan."""
        client = as_role(Role.ADMIN)

        # B1: Versi rubrik berbeda
        p1 = _rekaman("P1", [_nilai(1, "0.5", "0.5")], rubrik=RUBRIK_A)
        p2 = _rekaman("P2", [_nilai(1, "0.5", "0.5")], rubrik=RUBRIK_B)
        pasang_pembaca(p1, p2)
        resp1 = client.post(URL_REPORTS, json=_isi_request([p1, p2]))
        assert resp1.status_code == 409
        syarat1 = [r["requirement"] for r in resp1.json()["rejections"]]
        assert RUBRIC_VERSION in syarat1

        # B2: Model penilai berbeda
        p3 = _rekaman("P3", [_nilai(1, "0.5", "0.5")], judge_versi="judge-gpt4o")
        p4 = _rekaman("P4", [_nilai(1, "0.5", "0.5")], judge_versi="judge-claude")
        pasang_pembaca(p3, p4)
        resp2 = client.post(URL_REPORTS, json=_isi_request([p3, p4]))
        assert resp2.status_code == 409
        syarat2 = [r["requirement"] for r in resp2.json()["rejections"]]
        assert JUDGE_MODEL in syarat2

        # B3: Suite berbeda
        p5 = _rekaman("P5", [_nilai(1, "0.5", "0.5")], suite_ids=frozenset({SUITE_A}))
        p6 = _rekaman("P6", [_nilai(1, "0.5", "0.5")], suite_ids=frozenset({SUITE_B}))
        pasang_pembaca(p5, p6)
        resp3 = client.post(URL_REPORTS, json=_isi_request([p5, p6]))
        assert resp3.status_code == 409
        syarat3 = [r["requirement"] for r in resp3.json()["rejections"]]
        assert SUITE_VERSION in syarat3

    def test_viewer_dan_kontributor_meminta_skor_dev_ditolak_403(self, as_role, pasang_pembaca):
        """Negatif: AC12, AC21. Viewer dan Kontributor meminta skor dev lewat API -> 403."""
        p1 = _rekaman("P1", [_nilai(1, "0.5", "0.5")])
        p2 = _rekaman("P2", [_nilai(1, "0.5", "0.5")])
        pasang_pembaca(p1, p2)

        viewer = as_role(Role.VIEWER)
        kontributor = as_role(Role.CONTRIBUTOR)

        # Viewer meminta scope dev
        resp_v_dev = viewer.post(URL_REPORTS, json=_isi_request([p1, p2], score_scope="dev"))
        assert resp_v_dev.status_code == 403
        assert resp_v_dev.json()["code"] == REPORT_SCOPE_FORBIDDEN

        # Kontributor meminta scope dev_test
        resp_k_dev = kontributor.post(
            URL_REPORTS, json=_isi_request([p1, p2], score_scope="dev_test")
        )
        assert resp_k_dev.status_code == 403
        assert resp_k_dev.json()["code"] == REPORT_SCOPE_FORBIDDEN

        # Viewer meminta label mode code (hanya Admin yang boleh)
        resp_v_code = viewer.post(
            URL_REPORTS, json=_isi_request([p1, p2], product_label_mode="code")
        )
        assert resp_v_code.status_code == 403
        assert resp_v_code.json()["code"] == REPORT_LABEL_MODE_FORBIDDEN

    def test_laporan_dev_diterbitkan_ditolak_409(self, as_role, pasang_pembaca):
        """Negatif: D1, D9. Laporan dengan skor dev ditolak saat diterbitkan."""
        p1 = _rekaman("P1", [_nilai(1, "0.5", "0.5", SplitTag.DEV)])
        p2 = _rekaman("P2", [_nilai(1, "0.5", "0.5", SplitTag.DEV)])
        pasang_pembaca(p1, p2)

        admin = as_role(Role.ADMIN)
        post_resp = admin.post(URL_REPORTS, json=_isi_request([p1, p2], score_scope="dev"))
        assert post_resp.status_code == 201
        report_id = post_resp.json()["report"]["id"]

        # Act: Admin mencoba menerbitkan laporan dev
        pub_resp = admin.post(f"{URL_REPORTS}/{report_id}/publish")

        # Assert: 409 Conflict REPORT_HAS_DEV_SCORES
        assert pub_resp.status_code == 409
        assert pub_resp.json()["code"] == REPORT_HAS_DEV_SCORES

    def test_kurang_dari_dua_produk_atau_produk_duplikat_ditolak(self, as_role, pasang_pembaca):
        """Negatif: D2 B. Membandingkan < 2 produk atau produk duplikat ditolak."""
        prod_id = uuid.uuid4()
        s1 = _rekaman("P1", [_nilai(1, "0.5", "0.5")], product_id=prod_id)
        s2 = _rekaman("P1-Sesi2", [_nilai(1, "0.5", "0.5")], product_id=prod_id)
        pasang_pembaca(s1, s2)
        admin = as_role(Role.ADMIN)

        # Hanya 1 sesi
        resp_satu = admin.post(URL_REPORTS, json=_isi_request([s1]))
        assert resp_satu.status_code == 409
        assert NEED_AT_LEAST_TWO_PRODUCTS in resp_satu.json()["selection_errors"]

        # 2 sesi tapi produk sama (DUPLICATE_PRODUCT)
        resp_duplikat = admin.post(URL_REPORTS, json=_isi_request([s1, s2]))
        assert resp_duplikat.status_code == 409
        assert DUPLICATE_PRODUCT in resp_duplikat.json()["selection_errors"]


# ==============================================================================
# 3. PENGUJIAN EDGE (AC2, AC3, AC7, AC10, AC16)
# ==============================================================================


class TestQAEdge:
    def test_irisan_kasus_kosong_menghasilkan_peringatan_tanpa_angka(self, as_role, pasang_pembaca):
        """Edge: AC7. Irisan kasus kosong menampilkan peringatan tanpa angka perbandingan."""
        # P1 dan P2 memiliki snapshot kasus 1-4, namun P1 hanya menjawab 1,2 dan P2 hanya menjawab 3,4.
        p1 = _rekaman(
            "P1",
            [_nilai(1, "0.8", "0.8"), _nilai(2, "0.8", "0.8")],
            snapshot_tambahan=(_ref(3), _ref(4)),
        )
        p2 = _rekaman(
            "P2",
            [_nilai(3, "0.6", "0.6"), _nilai(4, "0.6", "0.6")],
            snapshot_tambahan=(_ref(1), _ref(2)),
        )
        pasang_pembaca(p1, p2)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([p1, p2]))

        # Assert: 200 OK dengan report=None dan warning="NO_SHARED_CASES"
        assert respons.status_code == 200
        isi = respons.json()
        assert isi["report"] is None
        assert isi["warning"] == NO_SHARED_CASES

    def test_produk_manual_tanpa_tingkat_kegagalan(self, as_role, pasang_pembaca):
        """Edge: AC2. Produk berjenis manual tidak memiliki tingkat kegagalan (NULL)."""
        otomatis = _rekaman(
            "AutoProduct",
            [_nilai(1, "0.8", "0.8")],
            session_type=SessionType.AUTOMATIC,
            calls={ScoreSplit.TEST: CallCounts(total=5, failed=1)},
        )
        manual = _rekaman(
            "ManualProduct",
            [_nilai(1, "0.7", "0.7")],
            session_type=SessionType.MANUAL,
            status=SessionStatus.FINISHED,
        )
        pasang_pembaca(otomatis, manual)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([otomatis, manual]))

        # Assert
        assert respons.status_code == 201
        sesi_dict = {s["product_label"]: s for s in respons.json()["report"]["sessions"]}

        # Otomatis: failure_rate terhitung
        assert sesi_dict["AutoProduct"]["measurement_kind"] == MeasurementKind.AUTOMATIC
        assert sesi_dict["AutoProduct"]["results"][0]["failure_rate"] == "20.00"

        # Manual: failure_rate bernilai None
        assert sesi_dict["ManualProduct"]["measurement_kind"] == MeasurementKind.MANUAL
        assert sesi_dict["ManualProduct"]["results"][0]["failure_rate"] is None

    def test_penanda_jumlah_percobaan_manual_berbeda_dan_rentang_tanggal(
        self, as_role, pasang_pembaca
    ):
        """Edge: AC10. Penanda 'jumlah percobaan berbeda' dan rentang tanggal manual > 7 hari."""
        auto = _rekaman("Auto", [_nilai(1, "0.8", "0.8")], repetisi=3)
        manual = _rekaman(
            "Manual",
            [_nilai(1, "0.7", "0.7")],
            session_type=SessionType.MANUAL,
            repetisi=1,
            dari=AWAL,
            sampai=AWAL + timedelta(days=8),  # > 7 hari
        )
        pasang_pembaca(auto, manual)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([auto, manual]))

        # Assert
        assert respons.status_code == 201
        sesi_manual = next(
            s for s in respons.json()["report"]["sessions"] if s["product_label"] == "Manual"
        )
        kode_penanda = {m["code"] for m in sesi_manual["markers"]}

        assert ReportMarkerCode.REPETITIONS_DIFFER in kode_penanda
        assert ReportMarkerCode.MANUAL_DATE_SPAN_OVER_7_DAYS in kode_penanda

    def test_penanda_versi_model_produk_berbeda(self, as_role, pasang_pembaca):
        """Edge: AC3. Versi model produk berbeda boleh dibandingkan dengan penanda jelas."""
        p1 = _rekaman("GPT-Old", [_nilai(1, "0.8", "0.8")], model="gpt-4-0314")
        p2 = _rekaman("GPT-New", [_nilai(1, "0.9", "0.9")], model="gpt-4-turbo")
        pasang_pembaca(p1, p2)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([p1, p2]))

        # Assert: 201 Created dan memiliki penanda model_version_differs
        assert respons.status_code == 201
        for sesi in respons.json()["report"]["sessions"]:
            kode_penanda = {m["code"] for m in sesi["markers"]}
            assert ReportMarkerCode.MODEL_VERSION_DIFFERS in kode_penanda

    def test_sesi_uji_coba_memiliki_penanda_is_trial(self, as_role, pasang_pembaca):
        """Edge: AC4. Sesi 'Uji Coba' wajib diberi penanda is_trial."""
        resmi = _rekaman("Resmi", [_nilai(1, "0.8", "0.8")], purpose=SessionPurpose.OFFICIAL)
        uji_coba = _rekaman("Trial", [_nilai(1, "0.7", "0.7")], purpose=SessionPurpose.TRIAL)
        pasang_pembaca(resmi, uji_coba)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL_REPORTS, json=_isi_request([resmi, uji_coba]))

        # Assert
        assert respons.status_code == 201
        sesi_map = {s["product_label"]: s for s in respons.json()["report"]["sessions"]}
        assert sesi_map["Resmi"]["is_trial"] is False
        assert sesi_map["Trial"]["is_trial"] is True

    def test_nilai_ulang_sesi_tidak_mengubah_laporan_tersimpan(self, as_role, pasang_pembaca):
        """Edge: AC16. Hasil laporan disimpan beku; nilai ulang sesi sumber tidak mengubah

        laporan lama.
        """
        # Sesi asli bernilai 0.80 dan 0.60
        p1 = _rekaman("P1", [_nilai(1, "0.80", "0.80")])
        p2 = _rekaman("P2", [_nilai(1, "0.60", "0.60")])
        pasang_pembaca(p1, p2)
        admin = as_role(Role.ADMIN)

        respons = admin.post(URL_REPORTS, json=_isi_request([p1, p2]))
        assert respons.status_code == 201
        report_id = respons.json()["report"]["id"]

        # Simulasi regrading pada sesi sumber di latar belakang:
        # P1 sekarang berubah menjadi 0.20 dan 0.20 pada pembaca sesi
        p1_regraded = _rekaman("P1", [_nilai(1, "0.20", "0.20")], session_id=p1.facts.session_id)
        pasang_pembaca(p1_regraded, p2)

        # Act: Buka kembali laporan yang sudah tersimpan
        buka_resp = admin.get(f"{URL_REPORTS}/{report_id}")
        assert buka_resp.status_code == 200

        # Assert: skor P1 pada laporan tetap 0.80 (beku, tidak berubah)
        sesi_p1 = next(s for s in buka_resp.json()["sessions"] if s["product_label"] == "P1")
        assert sesi_p1["results"][0]["det_score"] == "0.80"
        assert sesi_p1["results"][0]["llm_score"] == "0.80"
        assert sesi_p1["results"][0]["rank"] == 1
