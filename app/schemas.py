from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize(cls, value):
        return value.lower()


class UserOut(BaseModel):
    id: int
    username: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CollectionCreate(Input):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class CollectionOut(BaseModel):
    id: int
    name: str
    created_at: datetime


class DocumentCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)

    @field_validator("title", "text")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("value must not be blank")
        if "\x00" in value:
            raise ValueError("NUL characters are not allowed")
        return value


class DocumentSummary(BaseModel):
    id: int
    collection_id: int
    title: str
    content_hash: str
    status: Literal["queued", "indexing", "ready", "failed"]
    created_at: datetime
    updated_at: datetime
    chunk_count: int


class DocumentOut(DocumentSummary):
    text: str


class DocumentPage(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class JobOut(BaseModel):
    id: int
    document_id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    error: str | None
    created_at: float
    updated_at: float


class IngestOut(BaseModel):
    document: DocumentSummary
    job: JobOut


class SearchHit(BaseModel):
    document_id: int
    collection_id: int
    title: str
    chunk_number: int
    text: str
    snippet: str
    score: float


class SearchOut(BaseModel):
    query: str
    mode: str
    count: int
    hits: list[SearchHit]


class StatsOut(BaseModel):
    collections: int
    documents: int
    indexed_chunks: int
    document_statuses: dict[str, int]
