"""Kredensial provider tidak boleh pernah keluar lewat API. PBI-10 (SCRUM-75), QA (SCRUM-119).

AC-2 PBI-10: "Kredensial yang sudah disimpan tidak pernah ditampilkan
kembali kepada siapa pun, termasuk Admin sendiri." Matriks peran tim
menegaskannya: baris Read pada Provider Credential kosong untuk keempat
peran, dengan catatan "write-only, tidak bisa dibaca siapa pun termasuk
Admin (F2, L5)".

Aturan seperti itu biasanya dijaga per endpoint, satu per satu, dan bocor
justru lewat endpoint yang lupa diuji. Di sini penjagaannya dibalik:
seluruh permukaan API dibaca dari openapi.json, lalu setiap schema yang
dipakai sebagai respons diperiksa. Endpoint baru ikut terjaga otomatis
tanpa siapa pun perlu ingat menambah test.

Hari ini belum ada tabel provider, jadi test ini hijau. Nilainya muncul
nanti: begitu SCRUM-114 menambahkan kolom credential_encrypted dan
SCRUM-115 membuka endpoint registry, kebocoran sekecil apa pun langsung
merah di sini, bukan ditemukan saat demo ke klien.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

AKAR = Path(__file__).resolve().parents[3]
KONTRAK = AKAR / "openapi.json"

# Nama field yang tidak boleh muncul di respons mana pun. Dicocokkan ke
# nama properti, bukan ke nilainya, karena yang dijaga adalah bentuk
# kontrak: field yang tidak pernah ada di respons tidak bisa bocor.
POLA_RAHASIA = re.compile(
    r"(credential|secret|password|passphrase"
    r"|api[_-]?key|private[_-]?key|encryption[_-]?key"
    r"|access[_-]?token|refresh[_-]?token|authorization)",
    re.IGNORECASE,
)

# Pengecualian yang disebut spesifikasi, nama persis, bukan pola. SCRUM-115
# AC-2: GET produk AI "tanpa kredensial, hanya credential_hint &
# has_credential=true". credential_hint hanya 4 karakter terakhir
# (String(4), SCRUM-114), has_credential hanya penanda. Nama lain yang
# mirip, misalnya credential_value, tetap dianggap bocor.
DIIZINKAN = frozenset({"credential_hint", "has_credential"})


def _bocor(properti: str) -> bool:
    return POLA_RAHASIA.search(properti) is not None and properti not in DIIZINKAN


@pytest.fixture(scope="module")
def kontrak() -> dict[str, Any]:
    """openapi.json yang di-commit. CI menjaga berkas ini selalu sinkron dengan schema."""
    return json.loads(KONTRAK.read_text(encoding="utf-8"))


def _nama_schema_respons(kontrak: dict[str, Any]) -> set[str]:
    """Nama component schema yang dirujuk oleh blok responses mana pun."""
    ditemukan: set[str] = set()

    def telusuri(simpul: Any) -> None:
        if isinstance(simpul, dict):
            rujukan = simpul.get("$ref")
            if isinstance(rujukan, str) and rujukan.startswith("#/components/schemas/"):
                ditemukan.add(rujukan.rsplit("/", 1)[-1])
            for nilai in simpul.values():
                telusuri(nilai)
        elif isinstance(simpul, list):
            for nilai in simpul:
                telusuri(nilai)

    for operasi in kontrak["paths"].values():
        for isi in operasi.values():
            if isinstance(isi, dict):
                telusuri(isi.get("responses", {}))
    return ditemukan


def _properti(schema: dict[str, Any]) -> list[str]:
    """Nama properti satu schema, termasuk yang tersembunyi di anyOf/allOf/oneOf dan items."""
    nama: list[str] = list((schema.get("properties") or {}).keys())
    for kunci in ("anyOf", "allOf", "oneOf"):
        for cabang in schema.get(kunci, []) or []:
            if isinstance(cabang, dict):
                nama.extend(_properti(cabang))
    butir = schema.get("items")
    if isinstance(butir, dict):
        nama.extend(_properti(butir))
    return nama


def test_tidak_ada_field_kredensial_di_schema_respons(kontrak):
    """AC-2: tidak satu pun respons API memuat field yang berbau kredensial."""
    schemas = kontrak.get("components", {}).get("schemas", {})
    bocor: list[str] = []

    for nama in sorted(_nama_schema_respons(kontrak)):
        for properti in _properti(schemas.get(nama, {})):
            if _bocor(properti):
                bocor.append(f"{nama}.{properti}")

    assert not bocor, (
        "Field kredensial muncul di schema respons: "
        + ", ".join(bocor)
        + ". Kredensial provider write-only (PBI-10 AC-2, aturan L5): "
        "keluarkan dari schema respons, jangan cukup dikosongkan nilainya."
    )


def test_tidak_ada_field_kredensial_di_respons_yang_ditulis_inline(kontrak):
    """Penjagaan yang sama untuk respons yang schema-nya ditulis langsung, tanpa $ref."""
    bocor: list[str] = []

    for jalur, operasi in kontrak["paths"].items():
        for metode, isi in operasi.items():
            if not isinstance(isi, dict):
                continue
            for kode, respons in (isi.get("responses") or {}).items():
                for tipe in (respons.get("content") or {}).values():
                    schema = tipe.get("schema") or {}
                    if "$ref" in schema:
                        continue
                    for properti in _properti(schema):
                        if _bocor(properti):
                            bocor.append(f"{metode.upper()} {jalur} [{kode}] -> {properti}")

    assert not bocor, "Field kredensial muncul di respons inline: " + ", ".join(bocor)


def test_penjaga_ini_benar_benar_menangkap_kebocoran():
    """Penjaga yang tidak pernah bisa merah sama saja dengan tidak ada.

    Ejaan yang mungkin dipakai orang berbeda-beda, jadi polanya diuji
    langsung. Tanpa test ini, satu kesalahan ketik pada regex membuat dua
    test di atas lulus selamanya tanpa memeriksa apa pun.
    """
    harus_kena = [
        "credential",
        "credential_encrypted",
        "api_key",
        "apiKey",
        "client_secret",
        "encryption_key",
        "password",
        "access_token",
        "Authorization",
    ]
    for nama in harus_kena:
        assert POLA_RAHASIA.search(nama), f"pola gagal menangkap {nama!r}"

    boleh_lewat = ["id", "name", "base_url", "model_name", "rate_limit_rpm", "budget_idr"]
    for nama in boleh_lewat:
        assert not POLA_RAHASIA.search(nama), f"pola salah menuduh {nama!r}"


def test_pengecualian_hanya_nama_persis_dari_spesifikasi():
    """credential_hint dan has_credential boleh (SCRUM-115), nama mirip lainnya tidak."""
    for nama in DIIZINKAN:
        # Tanpa pengecualian, pola memang menangkapnya; jadi pengecualiannya bermakna.
        assert POLA_RAHASIA.search(nama)
        assert not _bocor(nama)

    for nama in ["credential_value", "credential_plain", "hint_credential", "credentials"]:
        assert _bocor(nama), f"{nama!r} seharusnya tetap dianggap bocor"


def test_kontrak_yang_diperiksa_memang_permukaan_api_yang_sekarang(kontrak):
    """Kalau openapi.json kosong atau salah baca, ketiga test di atas lulus semu."""
    assert kontrak["paths"], "openapi.json tidak memuat satu pun path"
    assert _nama_schema_respons(kontrak), "tidak ada schema respons yang terbaca dari kontrak"
