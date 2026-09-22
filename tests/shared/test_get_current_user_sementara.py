"""Test sementara untuk get_current_user versi placeholder.

Selama SCRUM-91 belum merge, get_current_user menolak semua request.
Test ini membuktikan penolakannya aman (401, bukan lolos diam-diam).

Begitu SCRUM-91 (PR #8) merge, get_current_user membaca cookie sesi dan
butuh parameter, lalu test ini skip sendiri. Perilaku barunya sudah
diuji di PR #8. Setelah itu file ini boleh dihapus.
"""

import inspect

import pytest

from app.shared.exceptions import DomainError
from app.shared.security import get_current_user


def test_placeholder_menolak_semua_request():
    if inspect.signature(get_current_user).parameters:
        pytest.skip("get_current_user sudah diganti SCRUM-91; diuji di PR #8")

    with pytest.raises(DomainError) as info:
        get_current_user()

    assert info.value.status_code == 401
