"""Nama tipe notifikasi yang dipakai pemanggil dan disimpan di kolom type.

review_assigned / review_decided sudah dipakai reviews (SCRUM-144/145).
review_needs_assignment: Admin saat kandidat reviewer kurang dari dua (SCRUM-196).
measurement_* dicadangkan untuk PBI-11; belum ada pemanggil di ticket ini.
"""

REVIEW_ASSIGNED = "review_assigned"
REVIEW_DECIDED = "review_decided"
REVIEW_NEEDS_ASSIGNMENT = "review_needs_assignment"
MEASUREMENT_COMPLETED = "measurement_completed"
MEASUREMENT_FAILED = "measurement_failed"
