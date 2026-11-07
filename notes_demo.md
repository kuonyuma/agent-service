# Agent Service 项目速览

项目是纯 Python 手写的 Gemini CLI Agent，不依赖大型 Agent 编排框架。
架构依据 `docs/framewokr.md` 拆分，当前不再提供 Web、数据库或容器部署链路。

## 技术栈

- Python 3.14、uv、Hatchling。
- Google GenAI SDK：模型流、函数调用及思考签名保留。
- prompt-toolkit / Rich：终端输入、审批与流式展示。
- PyYAML：配置；Pydantic：输出审核结构校验；httpx：网页工具。
- pytest / pytest-asyncio、Ruff、Pyright：回归与静态检查。

## 架构

五个内核模块分别负责运行、上下文、模型交互、工具调度、任务策略。
Gemini、具体工具、子代理、安全和 CLI 在 `adapters/` 中实现，
由 `bootstrap.py` 装配。内核可使用测试模型独立运行，不依赖配置或默认工具实例。
完整职责与文件对应关系见 [架构文档](docs/architecture.md)。

## 本地运行

```powershell
uv sync --frozen --group dev
$env:GEMINI_API_KEY = "替换为实际密钥"
& .venv\Scripts\python.exe -m agent_service.main
```

文件工具默认限制在启动目录内，写文件和执行命令需要人工批准。
网页搜索和抓取属于 CLI 工具，仍然保留。

CLI 历史目前仅在内存中；任意命令不是沙箱；输出审核不是显示前强制过滤。
详见 [安全边界](docs/safety.md) 和 [后续产品需求](docs/observability-requirements.md)。
