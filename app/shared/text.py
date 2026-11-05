"""Teks yang hanya berisi karakter kosong tak terlihat (SCRUM-214, catatan Adra di #89).

trim() PostgreSQL hanya membuang spasi biasa, dan str.strip() Python tidak membuang
zero-width space atau BOM. Akibatnya label atau alasan yang tampak kosong di layar tetap
tersimpan. Satu daftar di sini dipakai skema, service, dan CHECK database supaya
ketiganya sepakat; tests/integration/test_measurement_sessions_pg.py mencocokkan daftar
ini dengan database karakter per karakter.

Isinya: semua karakter yang str.isspace() (Python 3.13), ditambah karakter format
(kategori Cf) yang tidak terlihat: mongolian vowel separator, zero-width space,
non-joiner, joiner, word joiner, dan BOM. Hanya dibuang di tepi, jadi emoji yang
disambung zero-width joiner di tengah teks tetap utuh.
"""

BLANK_CHARS = (
    # Spasi ASCII dan pemisah kontrol yang dianggap spasi oleh str.isspace().
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f "
    # Spasi Unicode: NEL, NBSP, ogham, en/em/thin/hair space, pemisah baris dan paragraf,
    # narrow NBSP, medium math space, spasi ideografik.
    "\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
    # Karakter format tak terlihat (kategori Cf).
    "\u180e\u200b\u200c\u200d\u2060\ufeff"
)

# Literal SQL untuk trim(kolom, karakter); berlaku di PostgreSQL dan SQLite. Aman karena
# daftar di atas tidak memuat kutip atau backslash (dikunci tests/shared/test_text.py).
BLANK_CHARS_SQL = f"'{BLANK_CHARS}'"


def strip_blank(value: str) -> str:
    """Buang karakter kosong, termasuk yang tak terlihat, dari kedua tepi."""
    return value.strip(BLANK_CHARS)


def sql_not_blank(column: str) -> str:
    """Ekspresi CHECK: kolom masih berisi teks setelah karakter kosong di tepi dibuang."""
    return f"length(trim({column}, {BLANK_CHARS_SQL})) > 0"
