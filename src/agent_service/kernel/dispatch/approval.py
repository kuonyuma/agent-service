import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class ToolApprovalRequest:
    """以调用 ID、名称和参数指纹绑定一次审批；执行前重新校验。"""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    request_hash: str

    @classmethod
    def create(
        cls, call_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolApprovalRequest:
        snapshot = deepcopy(arguments)
        canonical = json.dumps(
            {"call_id": call_id, "tool_name": tool_name, "arguments": snapshot},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            call_id, tool_name, snapshot, sha256(canonical.encode("utf-8")).hexdigest()
        )


PermissionCheck = Callable[[ToolApprovalRequest], Awaitable[bool]]
