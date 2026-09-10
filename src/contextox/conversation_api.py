"""Workspace-scoped continuous conversation HTTP boundary."""
from fastapi import Query, Request, Response

from contextox import conversation_handoff
from contextox.handoff_models import ConversationHandoffRequest, ConversationHandoffReceipt
from contextox.models import (
    ConversationCreateRequest, ConversationMessagePage, ConversationMessageSendRequest,
    ConversationSubmissionReceipt, DiscussionTurn, WorkspaceConversation, WorkspaceError,
)
from contextox.store import Path2StateError, WorkspaceStoreError


def install_conversation_routes(app, get_store, get_runtime, error_response):
    root = "/api/workspaces/{workspace_id}/conversations"
    errors = {404: {"model": WorkspaceError}, 409: {"model": WorkspaceError},
              422: {"model": WorkspaceError}, 503: {"model": WorkspaceError}}

    @app.get(root, response_model=list[WorkspaceConversation], tags=["conversations"], responses=errors)
    def conversations(workspace_id: str, request: Request):
        try:
            return get_store(app).list_conversations(workspace_id)
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.post(root, response_model=WorkspaceConversation, status_code=201,
              tags=["conversations"], responses={**errors, 200: {"model": WorkspaceConversation}})
    def create_conversation(workspace_id: str, payload: ConversationCreateRequest,
                            request: Request, response: Response):
        try:
            result, created = get_store(app).create_conversation(workspace_id, payload)
            response.status_code = 201 if created else 200
            return result
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.get(root + "/{conversation_id}", response_model=WorkspaceConversation,
             tags=["conversations"], responses=errors)
    def conversation(workspace_id: str, conversation_id: str, request: Request):
        try:
            return get_store(app).get_conversation(workspace_id, conversation_id)
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.get(root + "/{conversation_id}/messages", response_model=ConversationMessagePage,
             tags=["conversations"], responses=errors)
    def conversation_messages(workspace_id: str, conversation_id: str, request: Request,
                              before_message_id: str | None = None, limit: int = Query(20, ge=1, le=50)):
        try:
            return get_store(app).list_conversation_messages(workspace_id, conversation_id,
                                                              before_message_id, limit)
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.post(root + "/{conversation_id}/messages", response_model=ConversationSubmissionReceipt,
              status_code=202, tags=["conversations"], responses={**errors, 200: {"model": ConversationSubmissionReceipt}})
    def send_conversation_message(workspace_id: str, conversation_id: str,
                                  payload: ConversationMessageSendRequest, request: Request, response: Response):
        try:
            result, created = get_runtime(app).send_conversation_message(workspace_id, conversation_id, payload)
            response.status_code = 202 if created else 200
            return result
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.get(root + "/{conversation_id}/submissions/{request_id}", response_model=ConversationSubmissionReceipt,
             tags=["conversations"], responses=errors)
    def conversation_submission(workspace_id: str, conversation_id: str, request_id: str, request: Request):
        try:
            result = get_store(app).conversation_submission(workspace_id, conversation_id, request_id)
            if result is None:
                raise Path2StateError("conversation_submission_not_found")
            return result
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.post(root + "/{conversation_id}/turns/{turn_id}/cancel", response_model=DiscussionTurn,
              tags=["conversations"], responses=errors)
    def cancel_discussion(workspace_id: str, conversation_id: str, turn_id: str, request: Request):
        try:
            return get_runtime(app).cancel_conversation_turn(workspace_id, conversation_id, turn_id)
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.post(root + "/{conversation_id}/handoffs", response_model=ConversationHandoffReceipt,
              status_code=202, tags=["conversations"], responses={**errors, 200: {"model": ConversationHandoffReceipt}})
    def handoff_answers(workspace_id: str, conversation_id: str, payload: ConversationHandoffRequest,
                        request: Request, response: Response):
        try:
            result, created = get_runtime(app).handoff_answers(workspace_id, conversation_id, payload)
            response.status_code = 202 if created else 200
            return result
        except WorkspaceStoreError as error:
            return error_response(request, error)

    @app.get(root + "/{conversation_id}/handoffs/{request_id}", response_model=ConversationHandoffReceipt,
             tags=["conversations"], responses=errors)
    def answer_handoff(workspace_id: str, conversation_id: str, request_id: str, request: Request):
        try:
            result = conversation_handoff.read(get_store(app), workspace_id, conversation_id, request_id)
            if result is None:
                raise Path2StateError("conversation_handoff_not_found")
            if result.analysis_state in {"claimed", "unknown"}:
                # Read the original durable execution identity after a lost
                # response. GET neither writes a handoff nor starts a worker.
                existing = get_store(app).message_submission(workspace_id, result.mission_id,
                    result.send_request.client_request_id, result.send_request)
                if existing is not None:
                    stage = conversation_handoff.recovered_analysis_state(existing.run)
                    result = result.model_copy(update={"run_id": existing.run.run_id,
                        "analysis_state": stage, "analysis_started": stage == "started",
                        "error_code": existing.run.error_code if stage == "failed" else None})
            return result
        except WorkspaceStoreError as error:
            return error_response(request, error)
