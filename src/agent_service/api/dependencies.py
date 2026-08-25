from typing import cast

from fastapi import Request

from agent_service.services.agent_api_service import AgentAPIService


def get_agent_api_service(request: Request) -> AgentAPIService:
    service = getattr(request.app.state, "agent_api_service", None)
    if service is None:
        raise LookupError("Agent API Service 尚未初始化。")
    return cast(AgentAPIService, service)
