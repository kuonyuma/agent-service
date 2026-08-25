from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from agent_service.api.dependencies import get_agent_api_service
from agent_service.api.schemas import (
    ApprovalDecisionRequest,
    ConversationResponse,
    CreateConversationRequest,
    CreateRunRequest,
    HealthResponse,
    MessageResponse,
    RunResponse,
    ToolCallResponse,
)
from agent_service.api.sse import encode_sse
from agent_service.api.streaming import ManagedStreamingResponse
from agent_service.services.agent_api_service import AgentAPIService

ServiceDependency = Annotated[AgentAPIService, Depends(get_agent_api_service)]

router = APIRouter()


@router.get(
    "/health/live",
    response_model=HealthResponse,
    tags=["health"],
    summary="进程存活检查",
)
async def health_live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
    tags=["health"],
    summary="数据库就绪检查",
)
async def health_ready(service: ServiceDependency) -> HealthResponse | JSONResponse:
    if await service.ready():
        return HealthResponse(status="ok")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready"},
    )


@router.post(
    "/api/v1/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
    summary="创建会话",
)
async def create_conversation(
    payload: CreateConversationRequest,
    service: ServiceDependency,
):
    return await service.create_conversation(payload.title)


@router.get(
    "/api/v1/conversations/{conversation_id}",
    response_model=ConversationResponse,
    tags=["conversations"],
    summary="读取会话",
)
async def get_conversation(
    conversation_id: str,
    service: ServiceDependency,
):
    return await service.get_conversation(conversation_id)


@router.get(
    "/api/v1/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
    tags=["conversations"],
    summary="按顺序读取会话消息",
)
async def list_messages(
    conversation_id: str,
    service: ServiceDependency,
):
    return await service.list_messages(conversation_id)


@router.post(
    "/api/v1/conversations/{conversation_id}/runs",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "example": (
                        "id: 1\n"
                        "event: tool.requested\n"
                        'data: {"run_id":"...","turn":1,'
                        '"tool_call_id":"call-1","tool_name":"write_file",'
                        '"requires_approval":true}\n\n'
                        "id: 2\n"
                        "event: tool.approval_required\n"
                        'data: {"run_id":"...","tool_call_id":"call-1",'
                        '"status":"pending","expires_at":"...Z"}\n\n'
                    )
                }
            },
            "description": "具名 SSE 事件流",
        },
        409: {"description": "会话已有活跃运行"},
    },
    tags=["runs"],
    summary="创建并流式执行 Agent run",
)
async def create_run(
    conversation_id: str,
    payload: CreateRunRequest,
    request: Request,
    service: ServiceDependency,
) -> ManagedStreamingResponse:
    # 必须在返回 StreamingResponse 前提交，404/409 才仍是 HTTP 状态。
    prepared = await service.prepare_run(
        conversation_id,
        payload.message,
        request_id=request.state.request_id,
    )

    async def frames() -> AsyncGenerator[bytes]:
        run_stream = service.stream_run(prepared)
        try:
            async for event in run_stream:
                yield encode_sse(event)
        finally:
            await run_stream.aclose()

    return ManagedStreamingResponse(
        frames(),
        on_incomplete=lambda: service.cancel_run(prepared),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Run-ID": prepared.run_id,
        },
    )


@router.get(
    "/api/v1/runs/{run_id}",
    response_model=RunResponse,
    tags=["runs"],
    summary="读取 run 的权威状态",
)
async def get_run(run_id: str, service: ServiceDependency):
    return await service.get_run(run_id)


@router.get(
    "/api/v1/runs/{run_id}/tool-calls",
    response_model=list[ToolCallResponse],
    tags=["tool-approvals"],
    summary="读取 run 的工具调用和审批审计",
)
async def list_tool_calls(run_id: str, service: ServiceDependency):
    return await service.list_tool_calls(run_id)


@router.post(
    "/api/v1/runs/{run_id}/tool-calls/{call_id}/approval",
    response_model=ToolCallResponse,
    responses={
        409: {"description": "审批已终结、已超时或运行不可审批"},
    },
    tags=["tool-approvals"],
    summary="批准或拒绝一次待审批工具调用",
)
async def decide_tool_call(
    run_id: str,
    call_id: str,
    payload: ApprovalDecisionRequest,
    service: ServiceDependency,
):
    return await service.decide_tool_call(
        run_id,
        call_id,
        payload.decision,
        payload.reason,
    )
