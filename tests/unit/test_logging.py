import json
import logging
from datetime import datetime
from typing import cast
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import Request, Response
from starlette.types import Scope

from agent_service.api.logging import JSONFormatter, request_id_middleware


def _request(*, request_id: str | None = None) -> Request:
    headers = []
    if request_id is not None:
        headers.append((b"x-request-id", request_id.encode()))
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health/live",
            "raw_path": b"/health/live",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "state": {},
        },
    )
    return Request(scope)


def test_json_formatter_outputs_structured_context_and_exception() -> None:
    try:
        raise ValueError("模拟异常")
    except ValueError as exc:
        record = logging.LogRecord(
            name="agent_service.runs",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="运行失败：%s",
            args=(exc,),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    record.__dict__.update(
        request_id="request-1",
        conversation_id="conversation-1",
        run_id="run-1",
    )
    payload = json.loads(JSONFormatter().format(record))

    assert payload["level"] == "ERROR"
    assert payload["logger"] == "agent_service.runs"
    assert payload["message"] == "运行失败：模拟异常"
    assert payload["request_id"] == "request-1"
    assert payload["conversation_id"] == "conversation-1"
    assert payload["run_id"] == "run-1"
    assert "ValueError: 模拟异常" in payload["exception"]
    assert datetime.fromisoformat(payload["timestamp"]).tzinfo is not None


@pytest.mark.asyncio
async def test_request_id_middleware_preserves_client_request_id_and_logs_it() -> None:
    request = _request(request_id="client-request-1")
    project_logger = Mock(spec=logging.Logger)

    async def call_next(received: Request) -> Response:
        assert received.state.request_id == "client-request-1"
        return Response(status_code=204)

    with patch("agent_service.api.logging.logging.getLogger", return_value=project_logger):
        response = await request_id_middleware(request, call_next)

    assert response.headers["X-Request-ID"] == "client-request-1"
    project_logger.info.assert_called_once_with(
        "%s %s -> %s",
        "GET",
        "/health/live",
        204,
        extra={"request_id": "client-request-1"},
    )


@pytest.mark.asyncio
async def test_request_id_middleware_generates_uuid7_when_header_is_missing() -> None:
    request = _request()

    async def call_next(received: Request) -> Response:
        return Response(content=received.state.request_id)

    with patch("agent_service.api.logging.logging.getLogger"):
        response = await request_id_middleware(request, call_next)

    request_id = response.headers["X-Request-ID"]
    assert UUID(request_id).version == 7
    assert bytes(response.body).decode() == request_id
