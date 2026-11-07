"""Gemini CLI Agent 的顶层包，也是阅读项目结构的起点。

项目分为三部分：kernel 定义运行机制与契约，adapters 提供外部实现，
config 保存配置与提示词；bootstrap.py 负责把三者装配起来。

入口与调用链：
- main.py 启动 CLI，通过 bootstrap.create_runner/create_app 创建运行对象。
- adapters.cli 接收输入，经 adapters.security 的协调器调用 kernel.runtime。
- runtime 编排上下文、模型、工具调度和任务策略，再将事件交回 CLI 展示。

建议先读 bootstrap.py，再读 kernel/runtime/runner.py 与 loop.py；
完整职责图见 docs/architecture.md。本包不提供 HTTP 服务或数据库持久化，
也不在包导入时创建客户端、运行器或工具实例。
"""
