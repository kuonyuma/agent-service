# AI Agent Framework

## 项目介绍

本项目是一个使用 `google-genai` 和 Python 从零构建的 AI 代理（AI Agent）框架。为了深入理解大模型的底层运作机制，本项目完全摒弃了 LangChain 等高度封装的外部库，通过原生的异步架构手动管理对话流、Function Calling 机制以及工具执行引擎。

### 快速上手

1. 安装依赖：确保安装了 `google-genai`, `prompt_toolkit`, `rich`。
2. 配置环境变量：
   ```bash
   export GEMINI_API_KEY="your_api_key_here"
   ```
3. 运行项目：
   ```bash
   python main.py
   ```

---

## 核心成就与技术挑战（项目经历摘要）

**项目背景：**
在 AI Agent 浪潮下，为了掌握模型与终端环境交互的最核心逻辑，独立设计并开发了该纯 Python 驱动的 Agentic 框架。主要应对了大模型流式截断、多轮自主思考循环以及系统权限沙箱等挑战。

**主要工作与技术贡献：**

* **构建纯异步事件流处理引擎 (Streaming Framework)**
  * 使用 Python `AsyncGenerator` 封装 Google GenAI 的流式响应。将非结构化的模型返回字节流实时解析，拆分为纯文本块与 `FunctionCall` 块，确保终端能在几毫秒内渲染模型首字，显著降低了用户的可感知延迟。
* **设计并实现自主调度循环状态机 (Agentic Loop)**
  * 编写了控制代理行为的核心层 (`core/agentic_loop.py`)，支持最高 10 轮的递归工具调用。
  * 实现了“请求-响应-工具拦截-结果回传”的闭环机制，使大语言模型能够基于工具返回的错误信息，自主反思并更正参数，进行下一次尝试。
* **搭建 Human-in-the-loop 安全拦截系统**
  * 在工具基类中抽象出 `read_only` 字段。针对 `EditFile` 等具有破坏性的本地操作工具，拦截其底层执行调用。
  * 通过 `prompt_toolkit` 中断异步队列，弹出交互式授权确认界面，将大模型的危险调用详情及参数展示给用户。如遭用户拒绝，主动构建包含错误原因的 `ToolResponse` 上报给模型，从而避免了“越权执行”并有效防止死循环。
* **灵活可扩展的终端交互架构**
  * 终端 UI 采用 `rich.Live` 结合 `Markdown` 组件实现实时富文本更新；同时设计了解耦的事件结构 (`StreamEvent` / `LoopEvent`)，使得 UI 层和底层大模型调用层做到无缝解耦。

**技术栈：** `Python 3.10+`, `google-genai`, `asyncio`, `prompt_toolkit`, `rich`
