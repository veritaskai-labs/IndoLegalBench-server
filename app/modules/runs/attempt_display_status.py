"""Status percobaan yang ditampilkan di laporan dan detail sesi. SCRUM-176 SCRUM-273.

PBI-22 D8: percobaan manual tetap pending di database dan ditandai terisi lewat filled_at.
Tampilan memperlakukan percobaan manual yang terisi sebagai succeeded, sama seperti hitungan
di daftar sesi. Status di database tidak diubah.
"""

from app.modules.runs.models import AttemptSource, AttemptStatus


def display_attempt_status(
    status: AttemptStatus | str,
    source: AttemptSource | str,
    *,
    filled: bool,
) -> AttemptStatus:
    """Hanya percobaan manual yang pending dan sudah terisi yang berubah menjadi succeeded."""
    current = AttemptStatus(status)
    is_manual = AttemptSource(source) == AttemptSource.MANUAL
    if is_manual and filled and current == AttemptStatus.PENDING:
        return AttemptStatus.SUCCEEDED
    return current
