"""Daily USD to IDR rate from JISDOR. SCRUM-76 SCRUM-206.

PBI-11 AC5: the rupiah rate is taken live every day. JISDOR is published
once per business day, so one fetch per day is enough and the last known
rate stands in when the service is down. HTTP is httpx.MockTransport, no
live network. The API key must never reach a log line.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from app.modules.runs import exchange_rate
from app.shared.exceptions import InternalError

URL = "https://rates.example.test/api/v1/central-bank/bi/latest"
KEY = "jisdor-test-key-9xQ2"  # pragma: allowlist secret
CADANGAN = Decimal("16000")
HARI_INI = date(2026, 10, 30)


class _Server:
    """Penangkap request palsu, urutan jawabannya diatur per test."""

    def __init__(self, *jawaban) -> None:
        self.jawaban = list(jawaban)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        jawab = self.jawaban.pop(0) if len(self.jawaban) > 1 else self.jawaban[0]
        if isinstance(jawab, Exception):
            raise jawab
        return jawab


def _ok(rate, **tambahan) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "bank": "BI",
            "rate_date": "2026-10-30",
            "source": "USD",
            "target": "IDR",
            "rate": rate,
            **tambahan,
        },
    )


class _Hari:
    def __init__(self, hari: date) -> None:
        self.hari = hari

    def __call__(self) -> date:
        return self.hari


def _adapter(server: _Server, *, hari=None, api_key=KEY, cadangan=CADANGAN):
    return exchange_rate.JisdorExchangeRate(
        url=URL,
        api_key=api_key,
        fallback=exchange_rate.ConfiguredExchangeRate(cadangan),
        transport=httpx.MockTransport(server),
        today=hari or _Hari(HARI_INI),
    )


def test_kurs_diambil_dengan_get_pasangan_usd_idr_dan_bearer_key():
    # Arrange
    server = _Server(_ok(16250.5))

    # Act
    kurs = _adapter(server).usd_idr()

    # Assert
    permintaan = server.requests[0]
    assert kurs == Decimal("16250.5")
    assert permintaan.method == "GET"
    assert permintaan.url.params["source"] == "USD"
    assert permintaan.url.params["target"] == "IDR"
    assert permintaan.headers["Authorization"] == f"Bearer {KEY}"


def test_kurs_berupa_string_dibaca_sebagai_decimal_tepat():
    # Arrange
    server = _Server(_ok("16250.50"))

    # Act
    kurs = _adapter(server).usd_idr()

    # Assert
    assert kurs == Decimal("16250.50")
    assert isinstance(kurs, Decimal)


def test_hari_yang_sama_hanya_satu_kali_memanggil_layanan():
    # Arrange
    server = _Server(_ok(16250.5))
    adapter = _adapter(server)

    # Act
    pertama = adapter.usd_idr()
    kedua = adapter.usd_idr()

    # Assert
    assert pertama == kedua == Decimal("16250.5")
    assert len(server.requests) == 1


def test_hari_berganti_mengambil_kurs_baru():
    # Arrange
    server = _Server(_ok(16250.5), _ok(16300))
    hari = _Hari(HARI_INI)
    adapter = _adapter(server, hari=hari)
    assert adapter.usd_idr() == Decimal("16250.5")

    # Act
    hari.hari = HARI_INI + timedelta(days=1)
    kurs = adapter.usd_idr()

    # Assert
    assert kurs == Decimal("16300")
    assert len(server.requests) == 2


def test_kurs_akhir_pekan_yang_ditandai_stale_tetap_dipakai():
    # Arrange: akhir pekan, layanan mengembalikan kurs terbit terakhir
    server = _Server(_ok(16250.5, stale=True, age_days=2))

    # Act
    kurs = _adapter(server).usd_idr()

    # Assert
    assert kurs == Decimal("16250.5")


@pytest.mark.parametrize(
    "jawaban",
    [
        httpx.Response(500, text="server error"),
        httpx.Response(401, json={"error": "invalid key"}),
        httpx.Response(200, text="bukan json"),
        httpx.Response(200, json=["bukan", "objek"]),
        httpx.Response(200, json={"source": "USD"}),
        httpx.Response(200, json={"rate": None}),
        httpx.Response(200, json={"rate": 0}),
        httpx.Response(200, json={"rate": -16000}),
        httpx.Response(200, json={"rate": "abc"}),
        httpx.ConnectError("tidak tersambung"),
        httpx.ReadTimeout("terlalu lama"),
    ],
    ids=[
        "http-500",
        "http-401",
        "bukan-json",
        "json-bukan-objek",
        "tanpa-rate",
        "rate-null",
        "rate-nol",
        "rate-negatif",
        "rate-bukan-angka",
        "gagal-sambung",
        "timeout",
    ],
)
def test_layanan_gagal_tanpa_riwayat_memakai_kurs_cadangan(jawaban):
    # Arrange
    server = _Server(jawaban)

    # Act
    kurs = _adapter(server).usd_idr()

    # Assert
    assert kurs == CADANGAN


def test_layanan_gagal_setelah_pernah_berhasil_memakai_kurs_terakhir_bukan_cadangan():
    # Arrange: kemarin berhasil, hari ini layanan mati
    server = _Server(_ok(16250.5), httpx.Response(503, text="down"))
    hari = _Hari(HARI_INI)
    adapter = _adapter(server, hari=hari)
    assert adapter.usd_idr() == Decimal("16250.5")
    hari.hari = HARI_INI + timedelta(days=1)

    # Act
    kurs = adapter.usd_idr()

    # Assert
    assert kurs == Decimal("16250.5")
    assert len(server.requests) == 2


def test_layanan_gagal_dan_cadangan_kosong_adalah_kesalahan_konfigurasi_500():
    # Arrange
    server = _Server(httpx.Response(500, text="server error"))
    adapter = _adapter(server, cadangan=None)

    # Act
    with pytest.raises(InternalError) as galat:
        adapter.usd_idr()

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "EXCHANGE_RATE_NOT_CONFIGURED"


def test_key_kosong_tidak_memanggil_layanan_dan_memakai_cadangan():
    # Arrange
    server = _Server(_ok(16250.5))

    # Act
    kurs = _adapter(server, api_key="").usd_idr()

    # Assert
    assert kurs == CADANGAN
    assert server.requests == []


def test_kegagalan_dicatat_sebagai_peringatan_tanpa_key_dan_tanpa_isi_respons(caplog):
    # Arrange: layanan menggaungkan key di badan error
    server = _Server(httpx.Response(401, text=f"invalid key {KEY}"))

    # Act
    with caplog.at_level(logging.WARNING):
        _adapter(server).usd_idr()

    # Assert
    catatan = [rekaman for rekaman in caplog.records if rekaman.levelno >= logging.WARNING]
    assert catatan, "kegagalan harus tercatat"
    assert any("401" in rekaman.getMessage() for rekaman in catatan)
    assert all(KEY not in rekaman.getMessage() for rekaman in caplog.records)


def test_kegagalan_transport_dicatat_tanpa_key(caplog):
    # Arrange
    server = _Server(httpx.ConnectError(f"gagal ke {KEY}"))

    # Act
    with caplog.at_level(logging.WARNING):
        _adapter(server).usd_idr()

    # Assert
    assert any(rekaman.levelno >= logging.WARNING for rekaman in caplog.records)
    assert all(KEY not in rekaman.getMessage() for rekaman in caplog.records)
