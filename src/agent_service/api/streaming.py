import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger("agent_service.api")


class ManagedStreamingResponse(StreamingResponse):
    """在内容未完整消费时执行 ASGI 生命周期级取消清理。"""

    def __init__(
        self,
        content: AsyncIterable[bytes],
        *,
        on_incomplete: Callable[[], Awaitable[None]],
        media_type: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._content_completed = False
        self._on_incomplete = on_incomplete
        super().__init__(
            self._track_completion(content),
            media_type=media_type,
            headers=headers,
        )

    async def _track_completion(
        self,
        content: AsyncIterable[bytes],
    ) -> AsyncIterator[bytes]:
        async for chunk in content:
            yield chunk
        self._content_completed = True

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._content_completed:
                try:
                    await self._on_incomplete()
                except Exception:
                    logger.exception("未完成流的取消清理失败")
