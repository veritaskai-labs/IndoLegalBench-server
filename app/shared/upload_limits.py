"""Batas ukuran unggahan multipart yang dibaca manual dari Request.

Starlette menampung seluruh body multipart sebelum handler membaca berkasnya, jadi unggahan
raksasa ditolak dari Content-Length lebih dulu (reject_oversized). Tanpa header itu (chunked),
capped_body menghitung body saat diterima dan menghentikannya begitu lewat batas; parser multipart
menutup berkas sementaranya sendiri.
"""

from collections.abc import Callable

from starlette.requests import Request
from starlette.types import Message

# Ruang untuk boundary dan header multipart di atas batas berkas.
MULTIPART_OVERHEAD = 64 * 1024

TooLarge = Callable[[int], Exception]


def reject_oversized(request: Request, limit_bytes: int, too_large: TooLarge) -> None:
    """Tolak dari Content-Length; header yang tidak ada atau bukan angka diserahkan ke capped_body."""
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        return
    if length > limit_bytes + MULTIPART_OVERHEAD:
        raise too_large(limit_bytes)


def capped_body(request: Request, limit_bytes: int, too_large: TooLarge) -> Request:
    """Request baru yang body-nya berhenti dibaca begitu melewati batas."""
    cap = limit_bytes + MULTIPART_OVERHEAD
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > cap:
            raise too_large(limit_bytes)
        return message

    return Request(request.scope, receive)
