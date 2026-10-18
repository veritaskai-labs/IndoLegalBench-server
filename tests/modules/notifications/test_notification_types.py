"""Konstanta tipe notifikasi (SCRUM-153)."""

from app.modules.notifications.types import (
    MEASUREMENT_COMPLETED,
    MEASUREMENT_FAILED,
    REVIEW_ASSIGNED,
    REVIEW_DECIDED,
)


def test_tipe_review_dan_measurement_dicadangkan():
    # Arrange / Act / Assert
    assert REVIEW_ASSIGNED == "review_assigned"
    assert REVIEW_DECIDED == "review_decided"
    assert MEASUREMENT_COMPLETED == "measurement_completed"
    assert MEASUREMENT_FAILED == "measurement_failed"
