"""Strict public request and generated-response contracts."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal["track_order", "cancel_request", "human_handoff", "out_of_scope"]


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCase(StrictModel):
    message: str = Field(min_length=1, max_length=6000)
    order_ref: str | None = Field(default=None, max_length=100)

    @field_validator("message", "order_ref")
    @classmethod
    def database_safe_text(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("Text cannot contain a null character")
        return value

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value):
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


class ResolveRequest(StrictModel):
    expected_case_version: int = Field(ge=1, le=2147483647, strict=True)


class ApproveRequest(ResolveRequest):
    proposal_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    expected_order_version: int = Field(ge=1, le=2147483647, strict=True)


class CaseContextRequest(ResolveRequest):
    order_ref: str = Field(min_length=1, max_length=100)

    @field_validator("order_ref")
    @classmethod
    def valid_reference(cls, value):
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("A valid order reference is required")
        return value


class RejectProposalRequest(ResolveRequest):
    proposal_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    reason_code: Literal["policy_concern", "customer_requested", "needs_manual_review", "other"]


class DemoLogin(StrictModel):
    actor: str = Field(min_length=1, max_length=80)


class EvaluationRequest(StrictModel):
    release_id: UUID
    dataset_key: Literal["fixture-v1"]
    mode: Literal["deterministic_invariants", "live_model"]


class Citation(StrictModel):
    chunk_id: str
    document_id: str
    corpus_id: str
    locator: str
    excerpt: str = Field(default="", max_length=2000)
    source_label: str | None = None
    title: str | None = None


class Draft(StrictModel):
    case_id: str
    case_version: int = Field(ge=1)
    intent: Intent
    disposition: Literal["resolved_read_only", "needs_info", "awaiting_approval", "escalated", "failed"]
    answer: str = Field(min_length=1, max_length=4000)
    citations: list[Citation]
    missing_information: list[str]
    proposal: dict | None = None
    reason_code: str | None = None
