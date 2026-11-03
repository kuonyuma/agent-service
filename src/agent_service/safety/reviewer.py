import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from google.genai import types
from pydantic import BaseModel, ConfigDict

from agent_service.client.client import get_client
from agent_service.config.settings import settings

SafetyCategory = Literal["none", "sexual", "violence", "child_safety"]


class ReviewDecision(BaseModel):
    """模型只提供判断和类别，不回传原文或自由文本理由。"""

    model_config = ConfigDict(extra="forbid", strict=True)

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
        response = await get_client().aio.models.generate_content(
            model=settings.model_config.name,
            contents=json.dumps({"text_to_review": text}, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=settings.safety_prompt,
                response_mime_type="application/json",
                # API 的 Schema 子集与本地严格校验分开，避免发送 additionalProperties。
                response_schema=types.Schema(
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
                temperature=0,
                max_output_tokens=1024,
            ),
        )
        usage: dict[str, int] = {}
        metadata = response.usage_metadata
        if metadata is not None:
            for target, source in (
                ("input_len", "prompt_token_count"),
                ("output_len", "candidates_token_count"),
            ):
                value = getattr(metadata, source, None)
                if isinstance(value, int):
                    usage[target] = value
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
