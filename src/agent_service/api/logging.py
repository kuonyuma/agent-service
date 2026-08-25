import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid7

from fastapi import Request, Response


class JSONFormatter(logging.Formatter):
    """输出便于容器采集的单行 JSON 日志。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "conversation_id", "run_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """只配置项目 logger，避免覆盖 Uvicorn 自己的日志策略。"""

    logger = logging.getLogger("agent_service")
    if any(isinstance(handler.formatter, JSONFormatter) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


async def request_id_middleware(request: Request, call_next) -> Response:
    request_id = request.headers.get("X-Request-ID", "").strip() or str(uuid7())
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("agent_service.api").info(
        "%s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
        extra={"request_id": request_id},
    )
    return response
