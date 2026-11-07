"""终端交互适配器：将用户操作接到运行协调器，并把事件呈现给用户。

app.py 的 App 负责终端输入、会话内存、流式文本与计划/工具状态展示，
并把修改工具的审批请求转换为用户确认。审核拒绝时清理当前输出，
并结束仍在等待的审批交互。

App 接收 bootstrap 已装配的 RunCoordinator，将历史和用户输入交给它，
再消费 LoopEvent/LoopResult 更新界面与会话；不会自行创建模型或运行循环。
任务规则属于 kernel.strategy，执行与授权检查属于 kernel.dispatch。
本包仅提供 CLI，会话历史不落数据库，也没有 HTTP 或 SSE 接口。
"""
