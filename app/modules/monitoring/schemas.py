"""Jawaban GET /ai-monitoring. Bukan data jejak."""

from pydantic import BaseModel


class MonitoringLink(BaseModel):
    """URL antarmuka Langfuse lokal, dan apakah health-nya menjawab."""

    url: str | None
    reachable: bool
