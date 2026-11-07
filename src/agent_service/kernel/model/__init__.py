"""模型交互内核：向运行时提供稳定的模型调用契约。

port.py 定义 ModelGateway 以及 StreamEvent/StreamResult；
外部模型适配器实现该端口，运行时无需知道认证和 SDK 请求细节。
interaction.py 的 ModelInteraction 消费模型流，校验最终结果，
整理文本与函数调用、保留原始 Part/思考签名，并累计模型用量。

输入是 context 整理的历史和 dispatch 筛选的工具声明；
输出交给 runtime，再由 dispatch 执行工具。这里不执行模型提出的调用，
也不读取配置或创建 Gemini 客户端。消息 DTO 目前仍使用 google.genai.types，
具体 SDK 转换和请求实现在 adapters.gemini。
"""
