from typing import Any, cast

from google.genai import types


def dump_content(content: types.Content) -> dict[str, Any]:
    """把 Gemini Content 转成可安全写入 JSON 列的普通字典。"""

    payload = content.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    return cast(dict[str, Any], payload)


def load_content(payload: dict[str, Any]) -> types.Content:
    """从数据库 JSON 恢复 Gemini Content。"""

    return types.Content.model_validate(payload)


def content_text(content: types.Content) -> str:
    """提取用于列表展示的文本，完整内容仍保存在 payload 中。"""

    return "".join(
        part.text or "" for part in (content.parts or []) if part.text is not None
    )


def content_kind(content: types.Content) -> str:
    """区分普通模型消息、模型工具请求和工具响应。"""

    parts = content.parts or []
    if any(part.function_response is not None for part in parts):
        return "tool_result"
    if any(part.function_call is not None for part in parts):
        return "tool_request"
    return "model_message" if content.role == "model" else "user_message"
