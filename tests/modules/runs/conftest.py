"""Fixture bersama tes runs.

siap (suite dengan kasus approved, satu produk otomatis, antrean kosong) ditulis di
test_start_endpoint.py. Didaftarkan di sini supaya tes QA SCRUM-213 memakainya tanpa
mengimpor fixture di tiap file.
"""

from tests.modules.runs.test_start_endpoint import siap  # noqa: F401
