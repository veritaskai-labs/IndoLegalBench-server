"""Contoh jawaban sintetis berisi beberapa kalimat untuk tes penilaian PBI-14.

Kalimatnya dikarang tim, bukan kutipan data klien. Yang ditiru hanya bentuk
rujukan yang lazim di jawaban hukum: singkatan dalam kurung, ayat tanpa kurung,
juncto, "undang-undang tersebut", "undang-undang yang sama", dan pasal kodifikasi
yang disebut sebelum sebuah UU. Nomor pasal dipilih untuk menguji bentuk itu,
bukan sebagai pendapat hukum.

Kodifikasi tanpa nomor dan tahun (KUHPerdata, HIR) ditulis sebagai teks bebas,
sama seperti editor PBI-21 menyimpannya karena tidak ada jenisnya di
regulation_types.
"""

from typing import Any

DATA_PRIBADI = (
    "Persetujuan pengguna atas syarat layanan mengikat seperti undang-undang bagi para "
    "pihak, sesuai Pasal 1338 KUHPerdata. Namun pemrosesan data pribadi tetap harus punya "
    "dasar yang sah menurut Pasal 20 Undang-Undang Nomor 27 Tahun 2022 tentang Pelindungan "
    "Data Pribadi. Bila terjadi kebocoran, pengendali data wajib memberi tahu pengguna "
    "paling lambat 3 x 24 jam sesuai Pasal 46 undang-undang yang sama."
)

AKTA_NOTARIS = (
    "Akta autentik dibuat oleh notaris yang kewenangannya diatur Undang-Undang Nomor 30 "
    "Tahun 2004 tentang Jabatan Notaris. Pasal 15 ayat (1) undang-undang tersebut memberi "
    "notaris kewenangan membuat akta autentik mengenai perbuatan dan perjanjian. Kewajiban "
    "notaris membacakan akta di hadapan para pihak diatur Pasal 16 UU Jabatan Notaris. "
    "Pasal 1870 KUHPerdata dan Pasal 165 Herzien Inlandsch Reglement (HIR) menyebutkan "
    "bahwa akta autentik merupakan bukti yang sempurna bagi para pihak."
)

BELANJA_DARING = (
    "Penjual daring wajib memberi informasi yang benar tentang barang yang ditawarkan, "
    "sesuai Pasal 7 ayat 1 huruf b Undang-Undang (UU) Nomor 8 Tahun 1999.\n\n"
    "Kegiatan dagangnya tunduk pada UU Nomor 7 Tahun 2014 (Perdagangan) juncto Peraturan "
    "Pemerintah (PP) Nomor 80 Tahun 2019 tentang Perdagangan Melalui Sistem Elektronik. "
    "Penjual wajib menyimpan bukti transaksi (Pasal 13 PP 80/2019) dan tidak boleh memasang "
    "iklan yang menyesatkan (Pasal 34 PP 80/2019). Bukti transaksi elektronik itu sah sebagai "
    "alat bukti menurut Pasal 5 UU Nomor 11 Tahun 2008. Bila barang tidak sesuai, pembeli "
    "dapat menuntut ganti rugi sesuai Pasal 19 UU 8/1999 juncto Pasal 65 UU Nomor 7 Tahun "
    "2014 tentang Perdagangan."
)


def link(
    regulation_type: str,
    number: str,
    year: int,
    pasal: str | None = None,
    *,
    connector: str = "none",
) -> dict[str, Any]:
    """Satu link PBI-21: name_only bila tanpa pasal."""
    return {
        "form": "pasal" if pasal else "name_only",
        "connector": connector,
        "regulation_type": regulation_type,
        "number": number,
        "year": year,
        "pasal": pasal,
    }


def rujukan(*links: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "structured", "links": list(links)}


def teks_bebas(isi: str) -> dict[str, Any]:
    return {"kind": "free_text", "free_text": isi, "links": []}


def sumber_akta_notaris() -> list[dict[str, Any]]:
    """Sumber acuan AKTA_NOTARIS: dua kodifikasi dan satu UU."""
    return [
        teks_bebas("Kitab Undang-Undang Hukum Perdata"),
        rujukan(link("uu", "30", 2004)),
        teks_bebas("Herzien Inlandsch Reglement"),
    ]


def sumber_belanja_daring() -> list[dict[str, Any]]:
    """Sumber acuan BELANJA_DARING. UU 11/2008 sengaja tidak ada (sitasi tambahan)."""
    return [
        rujukan(link("uu", "8", 1999)),
        rujukan(link("pp", "80", 2019)),
        rujukan(link("uu", "7", 2014)),
    ]
