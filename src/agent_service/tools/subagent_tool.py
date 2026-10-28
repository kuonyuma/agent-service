from typing import Any, ClassVar

from agent_service.core.subagent import SubagentManager
from agent_service.tools.base import Tool, ToolResult


class SubagentTool(Tool):
    """派发、续接、等待与管理特化子代理（Multi-Agent）的内置工具。"""

    name = "subagent"
    description = (
        "派发与协同特化子代理（Multi-Agent），每个子代理在隔离的上下文中独立运行并返回精炼报告。"
        "内置四个角色：'explorer'（只读代码探索与搜集证据，可在规划/反思阶段调用）、"
        "'worker'（密集任务编码实现与修复）、'monitor'（长时间任务/测试监控与验证）、"
        "'default'（完整读写与规划能力的通用子代理）。"
        "支持单轮并发调用多个子代理、通过 context_refs 零损耗传递前序子代理报告、"
        "通过 agent_id 续接已有子代理上下文，以及后台运行（background=True）与实时消息投递（action='send'/'wait'）。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "操作类型：'spawn'（默认，创建新子代理或续接已有 agent_id）、"
                    "'send'（向运行中或已完成的子代理发送追加指令）、"
                    "'wait'（等待后台子代理完成并汇总报告）、"
                    "'list'（列出当前所有子代理状态）"
                ),
            },
            "role": {
                "type": "STRING",
                "description": (
                    "子代理角色（action='spawn' 创建新子代理时使用）："
                    "'explorer'（只读探索/搜证）、'worker'（编码实现与修复）、"
                    "'monitor'（测试/命令监控与验证）、'default'（全功能通用）"
                ),
            },
            "task": {
                "type": "STRING",
                "description": "派发给子代理的具体任务说明或追加通信指令（action 为 spawn 或 send 时必填）",
            },
            "description": {
                "type": "STRING",
                "description": "简短任务标签（3-8 个字，如 '追踪登录鉴权调用链'）",
            },
            "agent_id": {
                "type": "STRING",
                "description": "目标子代理标识（如 'explorer-1'、'worker-1'），用于续接已有子代理上下文或向其发送实时指令",
            },
            "context_refs": {
                "type": "ARRAY",
                "description": "引用的前置子代理 ID 列表（如 ['explorer-1']），系统会自动将其完整报告注入目标子代理上下文",
                "items": {"type": "STRING"},
            },
            "background": {
                "type": "BOOLEAN",
                "description": "是否在后台异步启动子代理并立即返回（默认 False；设为 True 后可用 action='wait' 等待完成）",
            },
            "agent_ids": {
                "type": "ARRAY",
                "description": "action='wait' 时指定要等待的子代理 ID 列表（不传则等待所有后台子代理）",
                "items": {"type": "STRING"},
            },
        },
    }
    # 确保agent在 plan or reflection时可以调用subagent
    read_only = True
    
    def __init__(self, manager: SubagentManager | None = None) -> None:
        self.manager = manager

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        if self.manager is None:
            return ToolResult(
                content="子代理操作失败：当前运行上下文未绑定 SubagentManager。",
                is_error=True,
            )
        # 清洗参数
        raw_action = parameter.get("action", "spawn")
        action = (
            raw_action.strip().lower()
            if isinstance(raw_action, str) and raw_action.strip()
            else "spawn"
        )

        try:
            # 返回当前所有已创建子代理的状态概览
            if action == "list":
                return ToolResult(
                    content=self.manager.list_sessions(), 
                    is_error=False,
                )

            if action == "wait":
                # 读取参数并校验 
                raw_ids = parameter.get("agent_ids")
                if raw_ids is not None and not isinstance(raw_ids, list):
                    return ToolResult(
                        content="子代理操作失败：agent_ids 必须是字符串列表。",
                        is_error=True,
                    )
                
                single_id = parameter.get("agent_id", "")
                wait_ids: list[str] | None = None
                if isinstance(raw_ids, list):
                    wait_ids = [str(item) for item in raw_ids]
                elif isinstance(single_id, str) and single_id.strip():
                    wait_ids = [single_id.strip()]
                report = await self.manager.wait(agent_ids=wait_ids)
                return ToolResult(content=report, is_error=False)

            raw_refs = parameter.get("context_refs")
            if raw_refs is not None and not isinstance(raw_refs, list):
                return ToolResult(
                    content="子代理操作失败：context_refs 必须是子代理 ID 字符串列表。",
                    is_error=True,
                )
            
            context_refs = (
                [str(item) for item in raw_refs]
                if isinstance(raw_refs, list)
                else None
            )
            background = bool(parameter.get("background", False))
            raw_task = parameter.get("task", "")
            task = str(raw_task) if raw_task is not None else ""

            if action == "send":
                raw_agent_id = parameter.get("agent_id", "")
                agent_id = str(raw_agent_id) if raw_agent_id is not None else ""
                report = await self.manager.send(
                    agent_id=agent_id,
                    message=task,
                    context_refs=context_refs,
                    wait=not background,
                )
                return ToolResult(content=report, is_error=False)

            if action == "spawn":
                raw_role = parameter.get("role", "explorer")
                role = (
                    str(raw_role)
                    if raw_role is not None and str(raw_role).strip()
                    else "explorer"
                )
                raw_desc = parameter.get("description", "")
                description = str(raw_desc) if raw_desc is not None else ""
                raw_agent_id = parameter.get("agent_id", "")
                agent_id = str(raw_agent_id) if raw_agent_id is not None else ""
                report = await self.manager.spawn(
                    role=role,
                    task=task,
                    description=description,
                    agent_id=agent_id,
                    context_refs=context_refs,
                    background=background,
                )
                return ToolResult(content=report, is_error=False)

            return ToolResult(
                content=(
                    f"未知的子代理操作 action='{action}'，"
                    "支持的操作为：'spawn'、'send'、'wait'、'list'。"
                ),
                is_error=True,
            )
        except (ValueError, TypeError) as exc:
            return ToolResult(content=f"子代理操作失败：{exc}", is_error=True)
