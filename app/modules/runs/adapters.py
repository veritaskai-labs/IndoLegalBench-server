"""Port nyata untuk ringkasan, log panggilan, dan antrean sesi (PBI-11, SCRUM-206/207/209/244).

Modul runs hanya menjangkau cases, suites, dan providers lewat fungsi service
mereka, tidak lewat tabel atau repository.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.cases import service as cases_service
from app.modules.providers import service as providers_service
from app.modules.runs.call_log import month_start
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.session_ports import ProductCopy, SnapshotItem
from app.modules.runs.summary_ports import ProductInfo
from app.modules.suites import service as suites_service
from app.shared.exceptions import NotFoundError, ValidationError


class CaseCountAdapter:
    """Kasus approved per split tag, setelah memastikan semua suite ada."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def approved_counts(self, suite_ids: set[uuid.UUID]) -> Mapping[str, int]:
        suites_service.require_existing(self._db, suite_ids)
        counts = cases_service.count_approved_by_split(self._db, suite_ids)
        return {split.value: total for split, total in counts.items()}


class CaseTextAdapter:
    """Teks pertanyaan approved per split tag, setelah memastikan semua suite ada.

    Modul runs tidak membaca tabel cases. Pertanyaan diambil dari service cases.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def approved_texts(self, suite_ids: set[uuid.UUID]) -> Mapping[str, Sequence[str]]:
        suites_service.require_existing(self._db, suite_ids)
        texts = cases_service.approved_questions_by_split(self._db, suite_ids)
        return {split.value: questions for split, questions in texts.items()}


class ProductAdapter:
    """Produk AI sebagai ProductInfo, tanpa alamat dan kredensialnya.

    Tipe pengukuran dan harga output dibaca dari produk. Produk manual tidak
    punya provider dan model, jadi field itu bisa kosong. Harga kosong berarti
    service memakai katalog harga dari konfigurasi.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, product_id: uuid.UUID) -> ProductInfo | None:
        try:
            product = providers_service.get_product(self._db, product_id)
        except NotFoundError:
            return None
        return ProductInfo(
            id=product.id,
            name=product.name,
            measurement_type=product.measurement_type.value,
            is_active=product.is_active,
            provider_type=product.provider_type.value if product.provider_type else None,
            model_name=product.model_name,
            monthly_budget_idr=product.monthly_budget_idr,
            price_output_usd_per_mtok=product.price_output_usd_per_mtok,
            price_input_usd_per_mtok=product.price_input_usd_per_mtok,
        )


class SnapshotSourceAdapter:
    """Kasus yang dibekukan saat sesi berjalan (PBI-11 AC2, AC6).

    approved_copies_for_suite sudah memakai versi disetujui terbaru, termasuk
    untuk kasus yang sedang diedit, dan meninggalkan kasus yang belum pernah
    disetujui. Penyaringan dev/test dilakukan service.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def approved_items(self, suite_ids: Sequence[uuid.UUID]) -> list[SnapshotItem]:
        items: list[SnapshotItem] = []
        for suite_id in suite_ids:
            for copy in cases_service.approved_copies_for_suite(self._db, suite_id):
                items.append(
                    SnapshotItem(
                        case_id=copy["case_id"],
                        case_version_id=copy["case_version_id"],
                        split_tag=copy["body"]["sections"]["split_tag"],
                        content=copy["body"],
                    )
                )
        return items


class ProductCopyAdapter:
    """Salinan produk untuk sesi (D2 session_product_copies). Kredensial tidak dibaca."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def copy(self, product_id: uuid.UUID) -> ProductCopy:
        product = providers_service.get_product(self._db, product_id)
        if not product.is_active:
            # Ringkasan SCRUM-206 menolak produk nonaktif saat Mulai; ini menutup jeda antrean.
            raise ValidationError("Produk AI ini sedang nonaktif.", code="PRODUCT_INACTIVE")
        return ProductCopy(
            product_id=product.id,
            name=product.name,
            measurement_type=product.measurement_type.value,
            provider_type=product.provider_type.value if product.provider_type else None,
            base_url=product.base_url,
            model_name=product.model_name,
            model_params=product.model_params.model_dump() if product.model_params else None,
            price_input_usd_per_mtok=product.price_input_usd_per_mtok,
            price_output_usd_per_mtok=product.price_output_usd_per_mtok,
            monthly_budget_idr=product.monthly_budget_idr,
        )


class ProductLockAdapter:
    """Kunci produk lewat providers.service, di session database yang sama dengan INSERT sesi."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def lock_measurement_type(self, product_id: uuid.UUID) -> str:
        return providers_service.lock_for_new_session(self._db, product_id).value


class CallLogSpend:
    """Pengeluaran bulan ini dari call_logs."""

    def __init__(self, db: Session) -> None:
        self._rows = CallLogRepository(db)

    def spent_this_month_idr(self, product_id: uuid.UUID) -> Decimal:
        now = datetime.now(ZoneInfo("Asia/Jakarta"))
        return self._rows.sum_cost_since(product_id, month_start(now))


class CallLogAnswerHistory:
    """Rata-rata token jawaban dari call_logs."""

    def __init__(self, db: Session) -> None:
        self._rows = CallLogRepository(db)

    def average_answer_tokens(self, product_id: uuid.UUID) -> Decimal | None:
        return self._rows.output_token_average(product_id)


class NameAdapter:
    """Nama produk (termasuk yang dihapus) dan nama pembuat untuk daftar sesi."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def product_names(self, product_ids: set[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        return providers_service.product_names(self._db, product_ids)

    def user_names(self, user_ids: set[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        return auth_service.user_names(self._db, user_ids)
