"""Agent Service 的业务异常。"""


class AgentServiceError(Exception):
    """所有可预期业务异常的基类。"""


class ConfigurationError(AgentServiceError):
    """运行所需配置缺失或无效。"""


class AgentRuntimeError(AgentServiceError):
    """Agent 运行过程中出现无法继续的状态。"""


class InvalidToolCallError(AgentRuntimeError):
    """模型返回了无法执行的工具调用。"""


class SafetyBlockedError(AgentRuntimeError):
    """模型输出命中安全审核规则。"""


class SafetyReviewError(AgentRuntimeError):
    """无法可靠完成输出文本审核。"""
