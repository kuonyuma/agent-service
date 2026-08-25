import json

from agent_service.services.events import RunStreamEvent


def encode_sse(event: RunStreamEvent) -> bytes:
    """编码一个具名 SSE 事件；data 始终是一行合法 JSON。"""

    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return (f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n").encode()
