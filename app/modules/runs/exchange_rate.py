"""Kurs USD ke rupiah untuk perkiraan biaya (PBI-11 AC5, SCRUM-206)."""

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)

_JAKARTA = ZoneInfo("Asia/Jakarta")
_TIMEOUT = httpx.Timeout(10.0)


class ExchangeRateProvider(Protocol):
    """Sumber kurs. Service bergantung pada ini, bukan pada layanan tertentu."""

    def usd_idr(self) -> Decimal:
        """Rupiah per satu USD."""
        raise NotImplementedError


class ConfiguredExchangeRate:
    """Kurs tetap dari konfigurasi, cadangan sebelum kurs harian otomatis ada.

    Kurs kosong atau tidak positif adalah kesalahan konfigurasi server (500 dan
    log), bukan angka tebakan.
    """

    def __init__(self, rate: Decimal | None) -> None:
        self._rate = rate

    def usd_idr(self) -> Decimal:
        if self._rate is None or self._rate <= 0:
            logger.error("USD to IDR exchange rate is not configured")
            raise InternalError(
                "The USD to IDR exchange rate is not configured.",
                code="EXCHANGE_RATE_NOT_CONFIGURED",
            )
        return self._rate


def _jakarta_today() -> date:
    return datetime.now(_JAKARTA).date()


class JisdorExchangeRate:
    """Kurs JISDOR Bank Indonesia, diambil sekali per hari (PBI-11 AC5).

    JISDOR terbit sekali per hari kerja, jadi satu pengambilan per hari sudah
    cukup. Kalau layanan gagal, dipakai kurs terakhir yang tersimpan, lalu
    penyedia cadangan. Key dan isi respons tidak pernah dicatat di log: hanya
    jenis galat atau kode status.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        fallback: ExchangeRateProvider,
        transport: httpx.BaseTransport | None = None,
        today: Callable[[], date] = _jakarta_today,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._fallback = fallback
        self._transport = transport
        self._today = today
        self._last: tuple[Decimal, date] | None = None
        self._lock = threading.Lock()

    def usd_idr(self) -> Decimal:
        today = self._today()
        with self._lock:
            if self._last is not None and self._last[1] == today:
                return self._last[0]
            rate = self._fetch()
            if rate is not None:
                self._last = (rate, today)
                return rate
            if self._last is not None:
                return self._last[0]
        return self._fallback.usd_idr()

    def _fetch(self) -> Decimal | None:
        if not self._api_key:
            return None
        try:
            with httpx.Client(timeout=_TIMEOUT, transport=self._transport) as client:
                response = client.get(
                    self._url,
                    params={"source": "USD", "target": "IDR"},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as error:
            logger.warning("JISDOR request failed: %s", type(error).__name__)
            return None
        if response.status_code != 200:
            logger.warning("JISDOR returned HTTP %s", response.status_code)
            return None
        rate = _parse_rate(response)
        if rate is None:
            logger.warning("JISDOR response had no usable rate")
        return rate


def _parse_rate(response: httpx.Response) -> Decimal | None:
    """Ambil rate sebagai Decimal. Bukan angka, nol, atau negatif berarti tidak dipakai."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("rate")
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        return None
    return rate if rate.is_finite() and rate > 0 else None
