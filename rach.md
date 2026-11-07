# CLI 运行链路

```mermaid
flowchart TD
    MAIN["main.py"] --> BOOT["bootstrap.py：配置与显式装配"]
    BOOT --> CLI["adapters/cli：输入、审批、展示"]
    CLI --> SEC["adapters/security：RunCoordinator / 输出审核"]
    SEC --> RUN["kernel/runtime：AgentRunner / query"]
    RUN --> CTX["kernel/context：历史、通知、收件箱、压缩"]
    RUN --> MODEL["kernel/model：流与结果、用量"]
    RUN --> DISPATCH["kernel/dispatch：权限、阶段过滤、并发工具"]
    RUN --> STRATEGY["kernel/strategy：计划、反思、验证、完成"]
    MODEL --> PORT["ModelGateway 端口"]
    PORT -. "装配注入" .-> GEMINI["adapters/gemini：SDK 客户端与流"]
    DISPATCH --> TOOLS["注入的 Tool 注册表"]
    TOOLS -. "具体实现" .-> CONCRETE["adapters/tools"]
    TOOLS -. "每次运行绑定" .-> COLLAB["adapters/collaboration：子代理会话"]
    COLLAB -->|"注入 query_runner；父级权限交集"| RUN
    DISPATCH -->|"审批回调"| CLI
    RUN --> EVENTS["LoopEvent / LoopResult"]
    EVENTS --> SEC
    SEC --> CLI
    RUN --> CONTROL["RunControl：停止门闩、完整历史检查点"]
    SEC --> CONTROL
    COLLAB --> CONTROL
```

## 一轮用户输入

1. CLI 将内存历史和用户输入交给 Coordinator。
2. Coordinator 启动运行与输出审核，包裹审批回调；Runner 追加用户消息并按需压缩。
3. Runtime 按轮取得收件箱消息，策略确定阶段，调度器生成过滤后的工具声明。
4. 模型端口返回流式文本与完整结果；文本转成事件，调用 ID 补齐并去重。
5. 无工具调用时检查交付提醒；有调用时先审批，再并发执行并处理策略变化。
6. 函数调用与响应成对写入历史，再保存检查点；继续下一轮或到达 10 轮上限。
7. 正常终态等待剩余输出审核；阻断终态使用完整历史检查点。
8. CLI 保存返回的历史；退出时关闭 Runner 使用的模型客户端。

子代理共用底层模型适配器，但各自拥有历史、计划、收件箱和工具作用域；
可并行运行，不能递归创建子代理。角色、会话和上下文交接不属于 Runtime 内核。
`adapters/collaboration/bindings.py` 通过每次运行的绑定工厂注入这些能力。

此图中的适配器箭头表示装配后的调用关系，不表示内核反向导入适配器。
导入约束由 `tests/unit/test_architecture.py` 检查。没有 HTTP、数据库或持久化服务分支。
