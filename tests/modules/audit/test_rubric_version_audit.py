"""Audit versi rubrik. PBI-14 AC7, PBI-18 AC1, SCRUM-222 PR B.

PR A mengecualikan rubric_versions dari audit sampai ada penulisnya. POST
/admin/rubrics adalah satu-satunya penulis, jadi sekarang versi baru tercatat:
dibuat, diaktifkan, dan versi lama dinonaktifkan. Prompt penilai tidak ikut masuk
audit_logs (teks panjang yang sudah tersimpan di versinya sendiri), cukup nomor
versi, tanggal berlaku, dan penyetuju. Di halaman audit objeknya tampil sebagai
"Rubrik v<n>", bukan UUID.

Isi aspek, level, dan deskriptor tetap dikecualikan: hanya INSERT dan selalu ditulis
bersama versinya (tests/modules/audit/test_audit_catalog_guard.py).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: kejadian yang tidak boleh menulis catatan audit.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditEntityType, AuditLog
from app.modules.audit.trackers import Change, tracker_for
from app.modules.auth.models import User
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.grading.rubric_payload import body_rubrik

VERSI_ID = uuid.UUID("00000000-0000-0000-0000-000000000222")


def _baris(**kolom) -> SimpleNamespace:
    isi = {
        "id": VERSI_ID,
        "version_no": 3,
        "effective_date": date(2026, 10, 27),
        "approved_by_name": "Penyetuju Sintetis",
        "approved_at": date(2026, 10, 28),
        "judge_prompt_quality": "Prompt kualitas sintetis.",
        "judge_prompt_format": "Prompt format sintetis.",
        "is_active": False,
    } | kolom
    return SimpleNamespace(**isi)


# --- Tracker -------------------------------------------------------------------


def test_versi_baru_tercatat_tanpa_prompt():
    """Positif: rubric_version.created berisi nomor, tanggal, dan penyetuju saja."""
    tracker = tracker_for("rubric_versions")

    (event,) = tracker.created(_baris())

    assert tracker.entity_type == AuditEntityType.RUBRIC_VERSION
    assert (event.action, event.entity_id) == ("rubric_version.created", VERSI_ID)
    assert event.after == {
        "version_no": 3,
        "effective_date": "2026-10-27",
        "approved_by_name": "Penyetuju Sintetis",
        "approved_at": "2026-10-28",
    }


@pytest.mark.parametrize(
    ("lama", "baru", "aksi"),
    [(False, True, "rubric_version.activated"), (True, False, "rubric_version.deactivated")],
    ids=["aktif", "nonaktif"],
)
def test_aktif_dan_nonaktif_tercatat_sebagai_event_sendiri(lama, baru, aksi):
    (event,) = tracker_for("rubric_versions").updated(
        _baris(is_active=baru), {"is_active": Change(lama, baru)}
    )

    assert (event.action, event.before, event.after) == (
        aksi,
        {"is_active": lama},
        {"is_active": baru},
    )


def test_penguncian_saja_tidak_tercatat():
    """Negatif: locked_at diisi bersamaan dengan aktivasi, jadi tidak perlu event kedua."""
    tracker = tracker_for("rubric_versions")

    assert "locked_at" not in tracker.fields
    assert tracker.updated(_baris(), {}) == []


# --- Lewat endpoint ------------------------------------------------------------


@pytest.fixture
def admin(as_role, db_session):
    db_session.add(User(id=USER_ID_QA, email="qa@veritask.test", name="Pengguna QA", role="admin"))
    db_session.commit()
    return as_role(Role.ADMIN)


def _catatan(db_session) -> list[tuple[str, uuid.UUID, uuid.UUID | None]]:
    rows = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == AuditEntityType.RUBRIC_VERSION.value)
        .order_by(AuditLog.id)
    )
    return [(row.action, row.entity_id, row.actor_user_id) for row in rows]


def test_dua_versi_tercatat_dengan_pelaku_admin(admin, db_session):
    """Positif: v1 dibuat dan aktif; v2 dibuat dan aktif, v1 dinonaktifkan."""
    v1 = admin.post("/admin/rubrics", json=body_rubrik()).json()["id"]
    v2 = admin.post("/admin/rubrics", json=body_rubrik()).json()["id"]

    catatan = _catatan(db_session)

    assert [(aksi, str(objek)) for aksi, objek, _ in catatan] == [
        ("rubric_version.created", v1),
        ("rubric_version.activated", v1),
        ("rubric_version.created", v2),
        ("rubric_version.deactivated", v1),
        ("rubric_version.activated", v2),
    ]
    assert {pelaku for _, _, pelaku in catatan} == {USER_ID_QA}


def test_prompt_penilai_tidak_pernah_masuk_audit(admin, db_session):
    """Negatif: tidak ada payload audit yang memuat teks prompt."""
    admin.post("/admin/rubrics", json=body_rubrik())

    payload = [
        (row.before or {}) | (row.after or {}) for row in db_session.scalars(select(AuditLog))
    ]

    assert payload
    assert all("judge_prompt_quality" not in isi for isi in payload)
    assert all("Prompt kualitas sintetis." not in str(isi) for isi in payload)


def test_versi_yang_ditolak_tidak_meninggalkan_catatan(admin, db_session):
    """Negatif: 422 tidak menyimpan versi, jadi juga tidak ada catatan audit."""
    respons = admin.post("/admin/rubrics", json=body_rubrik(approved_at="2026-10-27"))

    assert respons.status_code == 422
    assert _catatan(db_session) == []


def test_halaman_audit_menampilkan_label_rubrik(admin):
    """Positif: PBI-18 F-06, objeknya terbaca sebagai "Rubrik v1", bukan UUID."""
    admin.post("/admin/rubrics", json=body_rubrik())

    respons = admin.get("/audit-logs", params={"entity_type": "rubric_version"})

    assert respons.status_code == 200, respons.text
    label = {item["entity_label"] for item in respons.json()["items"]}
    assert label == {"Rubrik v1"}
