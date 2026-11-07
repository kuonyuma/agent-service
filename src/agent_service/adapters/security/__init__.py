"""安全适配层：约束文件访问，并协调模型输出审核与运行停止。

workspace.py 的 WorkspacePathPolicy 检查工作区相对路径、敏感路径和链接；
具体文件工具调用它执行路径校验。
reviewer.py 定义文本审核协议及 Gemini 实现，将模型输出转成审核决定。
coordinator.py 的 RunCoordinator 包装 AgentRunner，并发协调生成与审核，
在拒绝或审核失败时停止后续运行，使用完整历史检查点生成安全终态。

调用位置是 CLI -> RunCoordinator -> runtime；工具调用则单独经过内核审批。
本包不代替 kernel.dispatch 的逐次授权，也不提供操作系统级沙箱。
输出审核不是展示前的同步闸门，部分文本可能先显示；已批准的 shell
也不受文件工具路径策略约束。具体保证与限制见 docs/safety.md。
"""
