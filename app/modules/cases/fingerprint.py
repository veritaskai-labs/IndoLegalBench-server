"""Sidik jari isi kasus untuk mengenali kasus kembar saat impor. PBI-4 SCRUM-337.

Keputusan SA (D7, D8): kasus dianggap sudah ada bila judul dan pertanyaannya sama setelah
dinormalkan (huruf kecil, spasi dirapikan). ID tidak bisa dipakai karena dibuat sistem.
Sidik jari disimpan di cases.content_fingerprint dan dihitung dari versi yang sedang
dikerjakan, jadi ikut berubah saat judul atau pertanyaan draft diubah.
"""

import hashlib
import re

_SPASI = re.compile(r"\s+")


def _normal(teks: str | None) -> str:
    return _SPASI.sub(" ", teks or "").strip().casefold()


def content_fingerprint(title: str | None, question: str | None) -> str | None:
    """SHA-256 heksadesimal (64 karakter) dari judul dan pertanyaan yang dinormalkan.

    None bila keduanya kosong: draft tanpa judul dan pertanyaan tidak pernah dianggap
    kembar dengan draft kosong lain. Pemisah baris baru menjaga "ab" + "c" berbeda dari
    "a" + "bc".
    """
    judul, pertanyaan = _normal(title), _normal(question)
    if not judul and not pertanyaan:
        return None
    return hashlib.sha256(f"{judul}\n{pertanyaan}".encode()).hexdigest()
