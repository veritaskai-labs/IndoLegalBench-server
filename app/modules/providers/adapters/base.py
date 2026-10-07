"""Interface adapter untuk produk AI yang diukur.

PBI-10, sub task "[SA] ERD, connection test sequence & adapter design".

Tiap produk AI punya bentuk API yang berbeda, dan jumlahnya akan
bertambah. Dengan adapter, menambah produk baru berarti menambah satu
file di folder ini, bukan menambah percabangan if di tengah kode
pengukuran.

Cara menambah produk baru:
1. Buat file baru di folder ini, misalnya aiyu.py
2. Buat kelas yang mewarisi ProviderAdapter
3. Implementasikan test_connection dan ask
4. Daftarkan di registry adapter

TODO(PBI-10): implementasikan adapter pertama setelah daftar produk AI
dan tipe API dari Veritask diterima (dikunci 18 September).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectionTestResult:
    """Hasil uji koneksi. Jangan taruh kredensial di message atau error_category."""

    status: str
    latency_ms: int | None = None
    message: str | None = None
    # Kategori kegagalan yang mudah dipahami Admin. None jika status == "ok".
    error_category: str | None = None


@dataclass
class ProviderResponse:
    """Hasil satu panggilan ke produk AI.

    raw disimpan apa adanya sebagai bukti, sesuai kebutuhan
    ketertelusuran laporan. Jangan dibuang meskipun panggilannya gagal,
    karena error juga data.
    """

    text: str
    model_version: str
    latency_ms: int
    raw: dict
    error: str | None = None


class ProviderAdapter(ABC):
    """Kontrak yang harus dipenuhi setiap adapter produk AI."""

    name: str

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Uji koneksi sekali, dipanggil saat produk didaftarkan.

        AC PBI-10: Admin bisa menguji koneksi sebelum produk dipakai
        dalam pengukuran sungguhan. Hasilnya status, latency_ms, dan
        message — bukan bool, dan bukan kredensial.
        """

    @abstractmethod
    def ask(self, prompt: str) -> ProviderResponse:
        """Kirim satu pertanyaan hukum, kembalikan jawaban mentah.

        Jangan melakukan penilaian atau penskoran di sini. Adapter hanya
        bertugas berbicara dengan API luar. Penilaian jawaban ada di
        modul runs.
        """
