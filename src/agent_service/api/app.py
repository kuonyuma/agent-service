import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_service.api.logging import configure_logging, request_id_middleware
from agent_service.api.routes import router
from agent_service.config.api_settings import APISettings
from agent_service.config.settings import settings as runtime_settings
from agent_service.core.runner import AgentRunner
from agent_service.db import create_database
from agent_service.errors import (
    ApprovalConflictError,
    ConversationBusyError,
    ConversationNotFoundError,
    PersistenceError,
    RunNotFoundError,
    ToolCallNotFoundError,
)
from agent_service.services.agent_api_service import AgentAPIService
from agent_service.tools.index import create_web_tool_registry
from agent_service.tools.workspace import WorkspacePathPolicy

logger = logging.getLogger("agent_service.api")


def create_app(
    service: AgentAPIService | None = None,
    settings: APISettings | None = None,
) -> FastAPI:
    """创建应用；测试可注入 Service，生产由 lifespan 管理资源。"""

    configure_logging()
    current_settings = settings or APISettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if service is not None:
            app.state.agent_api_service = service
            yield
            return

        database = create_database(
            current_settings.database_url,
            echo=current_settings.sql_echo,
        )
        workspace_policy = WorkspacePathPolicy(current_settings.workspace_root)
        web_tool_registry = create_web_tool_registry(
            workspace_policy,
            enable_mutating_tools=current_settings.web_mutating_tools_enabled,
            enable_quality_tool=current_settings.web_quality_tool_enabled,
        )
        web_tool_rules = [
            "当前运行环境自动开放工作区内的读取和列目录工具。",
        ]
        if current_settings.web_mutating_tools_enabled:
            web_tool_rules.append(
                "写入和修改工具已开放，但每次调用都必须等待本地操作员审批。"
            )
        if current_settings.web_quality_tool_enabled:
            web_tool_rules.append(
                "质量检查只允许 ruff、pyright 固定动作，并且必须逐次审批。"
            )
        web_tool_rules.append(
            "不要请求任意命令，也不要访问工作区之外的路径。"
        )
        runner = AgentRunner(
            allowed_tool_names=frozenset(web_tool_registry),
            tool_registry=web_tool_registry,
            system_prompt=(
                runtime_settings.system_prompt
                + "\n\n## Web API 工具限制\n"
                + "\n".join(web_tool_rules)
            ),
        )
        app.state.agent_api_service = AgentAPIService(
            database,
            runner,
            run_timeout_seconds=current_settings.run_timeout_seconds,
            approval_timeout_seconds=(
                current_settings.approval_timeout_seconds
            ),
            approval_poll_interval_seconds=(
                current_settings.approval_poll_interval_seconds
            ),
            stream_event_buffer_size=current_settings.stream_event_buffer_size,
        )
        try:
            yield
        finally:
            await runner.close()
            await database.close()

    app = FastAPI(
        title="Agent Service API",
        version="0.1.0",
        description=(
            "手写 Gemini Agent Runtime 的本地 HTTP API。POST /runs 使用 SSE；"
            "默认只开放工作区内的只读工具；可在本地显式开启逐次审批的文件修改。"
        ),
        lifespan=lifespan,
    )
    app.middleware("http")(request_id_middleware)
    app.include_router(router)

    async def not_found_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    async def busy_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    async def persistence_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "运行状态持久化失败，请通过 run 查询确认状态。"},
        )

    async def database_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        logger.error(
            "数据库请求失败",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"request_id": request_id},
        )
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={
                "detail": "数据库暂不可用，请稍后重试。",
                "request_id": request_id,
            },
        )

    app.add_exception_handler(ConversationNotFoundError, not_found_handler)
    app.add_exception_handler(RunNotFoundError, not_found_handler)
    app.add_exception_handler(ToolCallNotFoundError, not_found_handler)
    app.add_exception_handler(ConversationBusyError, busy_handler)
    app.add_exception_handler(ApprovalConflictError, busy_handler)
    app.add_exception_handler(PersistenceError, persistence_handler)
    app.add_exception_handler(SQLAlchemyError, database_handler)
    return app
