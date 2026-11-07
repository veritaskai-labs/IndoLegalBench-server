"""Membuat sesi pengukuran manual. PBI-22 (SCRUM-176) AC1, AC2, AC10; SCRUM-230.

D8 "Admin membuat sesi": satu produk manual, suite dan filter dev/test/both, percobaan
1-5 (standar 1), lalu snapshot dibuat saat itu juga. Tidak ada antrean, biaya, maupun
anggaran (AC10), jadi sesi langsung Belum terisi dan semua kasus muncul tanpa jawaban.

Snapshot dan salinan produk memakai port yang sama dengan sesi otomatis (PBI-11), jadi
kasus yang dibekukan mengikuti aturan yang sama: versi disetujui terbaru, bertag sesuai
filter, urutan tetap.
"""

import uuid

from app.modules.runs.models import SessionStatus
from app.modules.runs.schemas import ManualStartRequest, ManualStartResponse
from app.modules.runs.service import items_for_filter
from app.modules.runs.session_ports import (
    ManualSessionStore,
    NewSession,
    ProductCopySource,
    ProductLock,
    SnapshotSource,
    SuiteGuard,
)
from app.shared.exceptions import ValidationError

_PESAN_PRODUK_OTOMATIS = (
    "Produk otomatis diukur lewat Mulai pengukuran. Sesi manual hanya untuk produk manual."
)


class ManualSessionService:
    def __init__(
        self,
        *,
        sessions: ManualSessionStore,
        snapshots: SnapshotSource,
        products: ProductCopySource,
        product_lock: ProductLock,
        suites: SuiteGuard,
    ) -> None:
        self._sessions = sessions
        self._snapshots = snapshots
        self._products = products
        self._product_lock = product_lock
        self._suites = suites

    def create(self, request: ManualStartRequest, *, created_by: uuid.UUID) -> ManualStartResponse:
        # Kontrak #90: kunci produk dan baca ulang jenisnya di transaksi yang sama dengan
        # INSERT sesi, supaya ganti jenis yang bersamaan tidak meloloskan produk otomatis.
        if self._product_lock.lock_measurement_type(request.product_id) != "manual":
            raise ValidationError(_PESAN_PRODUK_OTOMATIS, code="PRODUCT_NOT_MANUAL")
        # PRODUCT_INACTIVE dari ProductCopyAdapter: produk nonaktif tidak bisa dicatat.
        product = self._products.copy(request.product_id)
        # Suite arsip tidak bisa dipilih untuk pengukuran baru, manual maupun otomatis.
        self._suites.require_measurable(request.suite_ids)
        items = items_for_filter(
            self._snapshots.approved_items(request.suite_ids), request.case_filter
        )
        if not items:
            raise ValidationError(
                "Tidak ada kasus yang disetujui dan bertag sesuai pilihan di suite ini.",
                code="NO_ELIGIBLE_CASES",
            )
        session_id = self._sessions.create_manual(
            NewSession(
                label=request.label,
                purpose=request.purpose,
                product_id=request.product_id,
                suite_ids=tuple(request.suite_ids),
                case_filter=request.case_filter,
                repetitions=request.repeat_count,
                created_by=created_by,
                judge_provider_id=request.judge_provider_id,
            ),
            items=items,
            product=product,
        )
        return ManualStartResponse(
            session_id=session_id,
            status=SessionStatus.NOT_FILLED,
            case_count=len(items),
            repetitions=request.repeat_count,
            total_answers=len(items) * request.repeat_count,
        )
