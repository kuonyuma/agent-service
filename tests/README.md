# 测试结构

项目测试按外部依赖边界分成三层：

```text
tests/
├── unit/                    # 单元测试：单个工具或模块，使用模拟对象和临时目录
├── integration/             # 集成测试：工具注册表、执行器、Agentic Loop 等内部协作
└── external/                # 真实外部环境：操作系统 shell、网络和 Gemini API
```

## 默认测试

```powershell
& .venv\Scripts\python.exe -m pytest -q
```

默认执行 `unit` 和 `integration`，`external` 只收集不执行，避免意外启动 shell、访问网络或产生 API 费用。

## 按层运行

```powershell
# 只运行单元测试
& .venv\Scripts\python.exe -m pytest -m unit -q

# 只运行集成测试
& .venv\Scripts\python.exe -m pytest -m integration -q

# 显式运行真实外部环境测试
& .venv\Scripts\python.exe -m pytest --run-external -m external -q
```

`external` 中的 Gemini 测试需要 `GEMINI_API_KEY`；没有有效 key 时会跳过 Gemini 测试。真实 API 测试可能产生网络请求和费用，应在需要时单独执行。
