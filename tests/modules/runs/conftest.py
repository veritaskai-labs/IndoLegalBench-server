"""Fixture bersama tes runs.

siap (suite dengan kasus approved, satu produk otomatis, antrean kosong) ditulis di
test_start_endpoint.py. sepuluh (siap ditambah 7 kasus approved, total 10 kasus) ditulis
di test_measurement_scale_qa.py. Didaftarkan di sini supaya tes QA SCRUM-213 memakainya
tanpa mengimpor fixture di tiap file.

siap_manual (suite dengan kasus approved dan satu produk manual, PBI-22 SCRUM-230) ditulis
di test_manual_session_endpoint.py dan dipakai tes status sesi manual.
"""

from tests.modules.runs.test_manual_session_endpoint import siap_manual  # noqa: F401
from tests.modules.runs.test_measurement_scale_qa import sepuluh  # noqa: F401
from tests.modules.runs.test_start_endpoint import siap  # noqa: F401
