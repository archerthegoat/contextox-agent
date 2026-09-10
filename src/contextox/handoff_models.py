"""Public, bounded schemas for an explicit reviewed-answer handoff."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictStr, model_validator
from contextox.models import (
    AnswerItem, ApprovedAnswerRef, ClarificationAnswerApproveRequest,
    ClarificationAnswerSaveRequest, ClarificationSubmissionReceipt, ContextOxModel,
    Count, DraftIdentity, Hash, ID, Key, MessageHistoryRef, MessageReferences,
    PositiveInt, SourceIdentity, TaskMessageSendRequest, UTC, canonical_sha256,
)


class HandoffSavedAnswer(ContextOxModel):
    version: PositiveInt
    sha256: Hash
    approval_id: ID | None = None


class HandoffReviewedAnswer(ContextOxModel):
    origin_run_id: ID
    clarification_id: ID
    request_sha256: Hash
    expected_latest_version: Count
    items: list[AnswerItem] | None = Field(default=None, min_length=1, max_length=20)
    saved_answer: HandoffSavedAnswer | None = None

    @model_validator(mode="after")
    def exactly_one_answer(self):
        if (self.items is None) == (self.saved_answer is None):
            raise ValueError("provide reviewed items or one exact saved answer")
        if self.saved_answer and self.saved_answer.version != self.expected_latest_version:
            raise ValueError("saved answer must match reviewed latest version")
        return self


class ConversationHandoffRequest(ContextOxModel):
    client_request_id: ID
    expected_conversation_version: PositiveInt
    expected_state_version: PositiveInt
    expected_draft: DraftIdentity
    source_refs: list[SourceIdentity] = Field(max_length=8)
    reviewed_answers: list[HandoffReviewedAnswer] = Field(min_length=1, max_length=50)
    content: Annotated[StrictStr, Field(min_length=1, max_length=4096)]
    references: MessageReferences
    history_messages: list[MessageHistoryRef] = Field(max_length=4)
    provider_send_confirmed: StrictBool

    @model_validator(mode="after")
    def fixed_review(self):
        keys = [(a.origin_run_id, a.clarification_id) for a in self.reviewed_answers]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate reviewed clarification")
        if not self.content.strip() or self.provider_send_confirmed is not True:
            raise ValueError("explicit content and sending confirmation required")
        if len({canonical_sha256(s) for s in self.source_refs}) != len(self.source_refs):
            raise ValueError("duplicate source")
        if len({h.message_id for h in self.history_messages}) != len(self.history_messages):
            raise ValueError("duplicate history")
        if len(self.model_dump_json().encode("utf-8")) > 262144:
            raise ValueError("handoff exceeds bounded review size")
        return self


class HandoffAnswerStep(ContextOxModel):
    origin_run_id: ID
    clarification_id: ID
    save_request: ClarificationAnswerSaveRequest | None
    save_receipt: ClarificationSubmissionReceipt | None
    approve_request: ClarificationAnswerApproveRequest | None
    approve_receipt: ClarificationSubmissionReceipt | None
    approved_answer: ApprovedAnswerRef


class ConversationHandoffReceipt(ContextOxModel):
    workspace_id: ID
    conversation_id: ID
    mission_id: ID
    client_request_id: ID
    request_sha256: Hash
    created_at: UTC
    answers_saved: Literal[True]
    answers_approved: Literal[True]
    analysis_started: StrictBool
    analysis_state: Literal["ready", "claimed", "started", "failed", "unknown"]
    send_request: TaskMessageSendRequest
    answer_steps: list[HandoffAnswerStep] = Field(min_length=1, max_length=50)
    run_id: ID | None = None
    error_code: Key | None = None

    @model_validator(mode="after")
    def analysis_consistency(self):
        if self.analysis_started != (self.analysis_state == "started"):
            raise ValueError("analysis_started must reflect the persisted start stage")
        if self.analysis_state == "started" and self.run_id is None:
            raise ValueError("started analysis requires exact run identity")
        return self
