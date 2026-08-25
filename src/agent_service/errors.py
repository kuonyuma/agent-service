"""Agent Service 的业务异常。"""


class AgentServiceError(Exception):
    """所有可预期业务异常的基类。"""


class ConfigurationError(AgentServiceError):
    """运行所需配置缺失或无效。"""


class AgentRuntimeError(AgentServiceError):
    """Agent 运行过程中出现无法继续的状态。"""


class InvalidToolCallError(AgentRuntimeError):
    """模型返回了无法执行的工具调用。"""


class ConversationNotFoundError(AgentServiceError):
    """指定会话不存在。"""


class RunNotFoundError(AgentServiceError):
    """指定运行不存在。"""


class ToolCallNotFoundError(AgentServiceError):
    """指定工具调用不存在。"""


class ConversationBusyError(AgentServiceError):
    """会话已经存在一个活跃运行。"""


class ApprovalConflictError(AgentServiceError):
    """工具审批已终结、已超时或不处于可审批状态。"""


class PersistenceError(AgentServiceError):
    """运行状态无法可靠地持久化。"""
