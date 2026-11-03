# 异步输出审核

CLI 和 API 工厂都在 `AgentRunner` 外使用 `RunCoordinator`。协调器独占读取
Agent 事件，将 `text.delta` 同时交给界面和独立审核任务。审核器复用当前
Gemini 客户端、Key 和模型名称，使用 `config/safety_prompt.md`，不声明工具，
不进入 Agent 循环，也不关闭共享客户端。直接使用 `AgentRunner` 是底层接口，
本身不执行审核。

这是生成后的异步检测：文字可能在审核结果到达前短暂可见。它无法撤销用户
已经看到、复制或截屏的内容，无法撤销已经发生的文件修改或命令。线程内的
同步工具也不保证随协程取消而立即停止。当前只审核 `text.delta`，不审核
用户输入、工具参数/结果、计划字段或子代理内部文本。

## 配置与触发

可以在 `src/agent_service/config/config.yaml` 中增加以下可选配置；不写时
使用这些默认值。配置在导入时加载，修改后必须重启。

```yaml
safety:
  chunk_chars: 800
  interval_seconds: 1.5
  timeout_seconds: 15.0
  context_chars: 1200
  max_buffer_chars: 16000
```

达到待审字符数、最早待审文字的等待时间或 Agent 终态时触发审核。同次运行
最多一个请求在途；新文字继续累计。请求携带前一次已审文字末尾的有限上下文。
`max_buffer_chars` 包含在途请求对应的未审文字；超过上限、请求超时、网络错误、
模型拒绝、输出截断、JSON 无法解析或类别与结论矛盾时均停止运行。
这些参数是工程初值，不构成安全保证；有限上下文也不能保证识别跨长距离内容。

审核返回 `allow`、`block` 或 `error`。类别包括色情露骨/性剥削、暴力、儿童安全；
医学科普、新闻概述、儿童保护和求助等应按必要细节与目的判断，规则见审核提示词。
结构校验只能验证返回格式，不能证明模型判断正确。共用模型也会增加请求、延迟、
费用及限流竞争，并不等于接入专业安全分类器。

## 终止与界面

审核违规或失败会先封闭工具启动入口，再取消 Agent、审批输入及子代理。
工具在实际获得并发名额后再次检查终止门闩，因此已锁定拦截状态后不会启动新工具。
CLI 在回答期间使用终端备用屏幕，拦截后替换当前回答并取消真实 PromptSession
审批；退出备用屏幕后只打印最终保留的回答。非交互式终端不具备同样的清屏能力。

Web 沿用 `failed` 状态，无 Schema 迁移。SSE 先发送 `safety.blocked`，再发送
持久化后的唯一 `run.failed`；事件不带违规原文。例如：

```json
{"run_id":"...","turn":0,"message":"本次内容因安全限制无法展示，请调整请求后重试。","code":"safety_blocked","category":"violence"}
```

审核不可用时 `code` 为 `safety_review_failed`。终态 `error` 与运行查询里的
`error_code` 分别提供固定中文提示和 `SafetyBlockedError` / `SafetyReviewError`。
SSE 消费者需要自行替换对应回答。拦截后排队的文字和审批事件不再向客户端发送。
正常 Agent 终态先暂存，尾部审核通过后才公布成功。

## 历史和资源

运行过程中只在完整工具调用/响应批次和完整普通模型消息后保存内存检查点。
拦截时清除本次检查点中的普通模型文本及运行时提示，保留完整工具调用/响应对，
追加固定中止提示。没有完整检查点的新操作不伪造结果，提示用户核实可能的副作用。
CLI 后续上下文与 Web 的 `content`、`payload_json` 都使用这份处理后的历史。
工具调用参数和结果仍属于现有工具审计记录，不在此次文本审核范围内。

Web 终态使用原短事务流程取消待审批项、将未确认完成的工具标记为 `cancelled`，
并按当前 run ID 释放会话占用。共享客户端仍由 CLI/API 应用生命周期关闭。
消费者退出或连接断开会取消并等待两个任务及嵌套的模型流，避免遗留任务。

终态 `usage` 包含已取得元数据的主 Agent、子代理及审核请求量，`safety_usage`
单独给出审核部分。被取消或超时的请求不一定能取得用量，统计不代表完整账单。

## 验证

普通测试使用假的审核器验证并发、尾部、审批、缓冲区、历史及 CLI/Web 消费者：

```powershell
& .venv\Scripts\python.exe -m pytest -q
```

真实 Gemini 的少量中文允许/拦截样例和共享客户端并发检查需显式开启：

```powershell
& .venv\Scripts\python.exe -m pytest --run-external tests\external\test_gemini_safety.py -q
```

真实 MySQL 收尾与历史验证位于 `test_mysql_safety.py`，只接受名称以 `_test`
结尾的 `AGENT_SERVICE_TEST_DATABASE_URL`。缺少测试库时跳过，不用 SQLite 替代。
