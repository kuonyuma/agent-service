# 测试结构

```text
tests/
├── unit/          # 单模块、工具、架构依赖和策略状态
├── integration/   # 注入模型后的 Runtime、工具调度、审核和多代理协作
├── external/      # 显式启用的真实 shell、网络和 Gemini API
└── support.py     # 测试装配助手；不是生产代码的默认依赖
```

## 运行

```powershell
# 默认只执行单元与内部集成测试
& .venv\Scripts\python.exe -m pytest -q

# 按层执行
& .venv\Scripts\python.exe -m pytest -m unit -q
& .venv\Scripts\python.exe -m pytest -m integration -q

# 仅在明确需要真实外部环境时启用
& .venv\Scripts\python.exe -m pytest --run-external tests\external -q

# 静态检查
& .venv\Scripts\python.exe -m ruff check .
& .venv\Scripts\python.exe -m pyright
```

默认收集但跳过 external，避免意外启动 shell、访问网络或产生模型费用。
Gemini 外部测试从项目配置读取模型名，需要有效 `GEMINI_API_KEY` 或配置中的 key；
无有效 key 时跳过。没有数据库、HTTP 服务或容器测试。

## 关键回归

- `unit/test_architecture.py`：五个内核模块、外层依赖方向、旧链路与依赖删除。
- `unit/test_task_policy.py`：并发批次不能误核销修改，按路径验证，失败进入反思。
- `integration/test_kernel_contracts.py`：不依赖配置/客户端的模型注入、权限交集、
  审批参数不可篡改、调用 ID 与响应配对、10 轮硬上限、生成器与并发任务清理。
- `integration/test_agentic_loop.py`：规划、执行、反思、交付提醒。
- `integration/test_multi_agent_loop.py`：并发探索、上下文交接、子代理审批、用量合并。
- 审核相关测试：停止门闩、终态等待、历史检查点、CLI 审批取消。

文件副作用测试使用临时目录；不对真实用户目录做破坏性测试。
新增内核测试优先注入 `ModelGateway`，不要在内核添加配置单例或具体工具回退。
