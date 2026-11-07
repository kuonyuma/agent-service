"""具体工具集合：实现能力，不接管 Agent 的运行循环或权限决策。

文件工具负责目录、读写、编辑和 YAML；run_command/quality_check 执行命令或固定检查，
web_search/fetch_web_page 提供联网能力。plan_tool、reflect_tool 和 subagent_tool
分别将模型调用接到任务计划、反思记录和子代理管理器。

index.py 的 create_tool_registry 创建独立工具表；新增工具须继承
kernel.dispatch.base.Tool 并在此注册，或通过组装入口传入自定义工具表。
计划、反思和子代理工具由 collaboration.bindings 按每次运行重新绑定。

调用由 kernel.dispatch 审批并调度，结果以 ToolResult 返回。
默认 CLI 的文件工具使用 security.WorkspacePathPolicy；命令工具不是沙箱，
不能把文件路径限制理解为对任意 shell 的隔离。联网工具不代表存在 Web 服务端。
"""
