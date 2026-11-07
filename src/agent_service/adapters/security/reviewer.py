import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from google.genai import types
from pydantic import BaseModel, ConfigDict

from agent_service.adapters.gemini.client import get_client
from agent_service.config.settings import settings

SafetyCategory = Literal["none", "sexual", "violence", "child_safety"]


class ReviewDecision(BaseModel):
    """模型只提供判断和类别，不回传原文或自由文本理由。"""

    model_config = ConfigDict(
        extra="forbid", # 拒绝额外字段
        strict=True # 严格校验
    )

    verdict: Literal["allow", "block"]
    category: SafetyCategory


@dataclass(frozen=True)
class ReviewResult:
    verdict: Literal["allow", "block", "error"]
    category: SafetyCategory = "none"
    usage: dict[str, int] = field(default_factory=dict)


class TextReviewer(Protocol):
    async def check(self, text: str) -> ReviewResult: ...


class GeminiTextReviewer:
    """复用共享客户端和模型；超时及任务生命周期由运行协调器管理。"""

    async def check(self, text: str) -> ReviewResult:
        response = await get_client().aio.models.generate_content( # 这里不是异步回答
            model=settings.model_config.name,
            contents=json.dumps({"text_to_review": text}, ensure_ascii=False), # 序列化,把 Python 对象转换为 JSON 格式的字符串
            config=types.GenerateContentConfig(
                system_instruction=settings.safety_prompt,
                response_mime_type="application/json", # 告诉模型输出格式为json
                response_schema=types.Schema( # 指定json里的字段
                    type=types.Type.OBJECT,
                    properties={
                        "verdict": types.Schema(
                            type=types.Type.STRING, enum=["allow", "block"]
                        ),
                        "category": types.Schema(
                            type=types.Type.STRING,
                            enum=["none", "sexual", "violence", "child_safety"],
                        ),
                    },
                    required=["verdict", "category"],
                ),
                temperature=1, # 这里官方推荐温度为1
                max_output_tokens=1024,
            ),
        )
        usage: dict[str, int] = {}
        metadata = response.usage_metadata
        if metadata is not None:
            input_tokens = getattr(metadata, "prompt_token_count", None)
            if isinstance(input_tokens, int):
                usage["input_len"] = input_tokens

            output_tokens = getattr(metadata, "candidates_token_count", None)
            if isinstance(output_tokens, int):
                usage["output_len"] = output_tokens
        candidates = response.candidates or []
        if (
            not candidates
            or candidates[0].finish_reason != types.FinishReason.STOP
            or not response.text
        ):
            return ReviewResult("error", usage=usage)
        try:
            decision = ReviewDecision.model_validate_json(response.text)
        except ValueError:
            return ReviewResult("error", usage=usage)
        if (decision.verdict == "allow") != (decision.category == "none"):
            return ReviewResult("error", usage=usage)
        return ReviewResult(decision.verdict, decision.category, usage)
