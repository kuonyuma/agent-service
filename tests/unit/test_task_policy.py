"""验证状态以整批并发结果为边界，而不是以工具声明顺序为边界。"""

import pytest

from agent_service.kernel.dispatch.base import ToolResult
from agent_service.kernel.dispatch.executor import ToolExecution
from agent_service.kernel.strategy.policy import TaskPolicy


def execution(name: str, path: str = "", *, failed: bool = False) -> ToolExecution:
    return ToolExecution(
        name, name, {"path": path}, ToolResult("结果", is_error=failed)
    )


@pytest.mark.parametrize(
    "verification", ["read_file", "run_quality_check", "run_command"]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_same_batch_verification_never_clears_a_concurrent_write(verification, reverse):
    policy = TaskPolicy(frozenset())
    batch = [execution("write_file", "a.py"), execution(verification, "a.py")]
    if reverse:
        batch.reverse()

    policy.observe(batch, set(), 1)

    assert policy.plan.unverified_edits == ["a.py"]
    policy.observe([execution(verification, "a.py")], set(), 2)
    assert not policy.plan.needs_verification()


def test_readback_only_verifies_its_own_target():
    policy = TaskPolicy(frozenset())
    policy.observe(
        [execution("edit_file", "a.py"), execution("write_file", "b.py")], set(), 1
    )
    policy.observe([execution("read_file", "a.py")], set(), 2)

    assert policy.plan.unverified_edits == ["b.py"]


def test_failed_quality_check_does_not_clear_edits_and_enters_reflection():
    policy = TaskPolicy(frozenset({"reflect"}))
    policy.plan.record_edit("a.py")

    decision = policy.observe([execution("run_quality_check", failed=True)], set(), 1)

    assert policy.plan.unverified_edits == ["a.py"]
    assert policy.plan.phase == "reflecting"
    assert "run_quality_check" in decision.notice
    assert [event.type for event in decision.events] == ["reflection.updated"]


def test_denied_mutation_does_not_create_verification_or_failure_state():
    policy = TaskPolicy(frozenset({"reflect"}))

    decision = policy.observe(
        [execution("write_file", "a.py", failed=True)], {"write_file"}, 1
    )

    assert not policy.plan.needs_verification()
    assert policy.plan.phase == "executing"
    assert not decision.notice
