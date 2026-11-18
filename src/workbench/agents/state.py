"""Local evidence payloads retained for prompt building and date utilities."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievedChunkSummary(BaseModel):
    """Lightweight summary of a retrieved chunk (before opening)."""

    chunk_id: str
    score: float
    title: str = ""
    snippet: str = Field(default="", description="First N chars of chunk text")


class OpenedChunk(BaseModel):
    """Full chunk data after the agent has 'opened' it."""

    chunk_id: str
    document_id: str = ""
    title: str = ""
    section: str | None = None
    text: str = ""


class TimelineItem(BaseModel):
    """A single event in a timeline extracted from evidence."""

    date: str = Field(..., description="Date or date range string")
    event: str = Field(..., description="Description of the event")
    supporting_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Chunk IDs that support this timeline entry",
    )
