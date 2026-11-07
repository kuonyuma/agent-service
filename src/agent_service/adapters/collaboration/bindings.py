from agent_service.adapters.collaboration.manager import QueryRunner, SubagentManager
from agent_service.adapters.tools.plan_tool import UpdatePlanTool
from agent_service.adapters.tools.reflect_tool import ReflectTool
from agent_service.adapters.tools.subagent_tool import SubagentTool
from agent_service.kernel.dispatch.approval import PermissionCheck
from agent_service.kernel.runtime.bindings import (
    EventForwarder,
    RunBindings,
    ToolRegistry,
)
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.strategy.plan import ExecutionPlan


class ToolBindingFactory:
    """把计划工具和协作工具绑定到本次运行，避免不同任务共享可变状态。"""

    def __init__(self, query_runner: QueryRunner) -> None:
        self.query_runner = query_runner

    def __call__(
        self,
        *,
        registry: ToolRegistry,
        allowed_tool_names: frozenset[str],
        system_prompt: str,
        plan: ExecutionPlan,
        permission_check: PermissionCheck | None,
        event_forwarder: EventForwarder,
        run_control: RunControl | None,
    ) -> RunBindings:
        active = dict(registry)
        if "update_plan" in active:
            active["update_plan"] = UpdatePlanTool(plan)
        if "reflect" in active:
            active["reflect"] = ReflectTool(plan)
        bindings = RunBindings(active)
        if "subagent" in allowed_tool_names:
            manager = SubagentManager(
                active,
                query_runner=self.query_runner,
                parent_allowed_tool_names=allowed_tool_names,
                parent_system_prompt=system_prompt,
                parent_plan=plan,
                permission_check=permission_check,
                event_forwarder=event_forwarder,
                run_control=run_control,
            )
            active["subagent"] = SubagentTool(manager)
            bindings.usage = manager.total_usage
            bindings.cleanup = manager.close
        return bindings
