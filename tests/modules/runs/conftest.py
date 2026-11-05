"""Fixture bersama tes runs.

siap (suite dengan kasus approved, satu produk otomatis, antrean kosong) ditulis di
test_start_endpoint.py. sepuluh (siap ditambah 7 kasus approved, total 10 kasus) ditulis
di test_measurement_scale_qa.py. Didaftarkan di sini supaya tes QA SCRUM-213 memakainya
tanpa mengimpor fixture di tiap file.
"""

from tests.modules.runs.test_measurement_scale_qa import sepuluh  # noqa: F401
from tests.modules.runs.test_start_endpoint import siap  # noqa: F401
