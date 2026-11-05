"""Aturan perpindahan status percobaan per D4 bagian B. PBI-12 AC2, AC3, SCRUM-214.

Pasangan tabel session_transitions untuk tingkat panggilan. Percobaan (attempts) ditulis
oleh beberapa jalur: worker mengambil dan menyelesaikan (SCRUM-249), coba ulang yang gagal
(SCRUM-218), dan nanti pemulihan restart (SCRUM-217). Tabel ini sumber kebenarannya;
migrasi e7a41c2b9d53 menyimpan salinan beku yang ditegakkan trigger PostgreSQL, jadi
jalur yang lupa memanggil ensure_attempt_transition tetap ditolak database.

Berlaku untuk percobaan otomatis. Pencatatan produk manual (PBI-22) belum dirancang.
"""

from app.modules.runs.models import AttemptStatus
from app.shared.exceptions import ConflictError

_A = AttemptStatus

ALLOWED_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    # Worker mengambil percobaan.
    _A.PENDING: frozenset({_A.RUNNING}),
    # Jawaban sah, atau gagal teknis (tanpa respons, error penyedia, timeout 240 detik, ...).
    _A.RUNNING: frozenset({_A.SUCCEEDED, _A.TECHNICAL_FAILURE}),
    # Hanya coba ulang manual (AC2, AC3). D4 menggambar panah ke running; dibuka ulang ke
    # pending supaya klaim worker tetap satu-satunya jalan masuk ke running.
    _A.TECHNICAL_FAILURE: frozenset({_A.PENDING}),
    # Jawaban sah tidak pernah diulang.
    _A.SUCCEEDED: frozenset(),
}


def ensure_attempt_transition(current: AttemptStatus | str, target: AttemptStatus | str) -> None:
    """Tolak perpindahan di luar D4 bagian B dengan 409. Menerima string dari database."""
    current, target = AttemptStatus(current), AttemptStatus(target)
    if target not in ALLOWED_ATTEMPT_TRANSITIONS[current]:
        raise ConflictError(
            f"Status percobaan tidak bisa berpindah dari {current.value} ke {target.value}.",
            code="INVALID_ATTEMPT_TRANSITION",
            details={"from": current.value, "to": target.value},
        )
