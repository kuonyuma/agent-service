"""运行时控制：把各内核模块组织成有界、可停止的一次任务。

主要入口与组成：
- runner.py 的 AgentRunner 接收历史和用户输入，准备上下文并调用循环。
- loop.py 的 query 编排模型请求、工具执行和策略反馈，单次最多 10 轮。
- events.py 定义 LoopEvent/LoopResult，供 CLI、审核协调器和子代理消费。
- control.py 保存停止状态和完整历史检查点，避免取消后留下孤立工具调用。
- bindings.py 定义按次绑定及清理端口，供外部绑定计划工具和子代理资源。

本包只负责流程与生命周期；历史处理、模型交互、工具规则和任务决策分别交给
context、model、dispatch、strategy。具体实现和配置由 bootstrap 注入。
"""
