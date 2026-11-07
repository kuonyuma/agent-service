"""Gemini 模型适配器：将 SDK 客户端接到内核的 ModelGateway 端口。

client.py 负责 API key 解析、客户端获取和关闭；key 优先来自 GEMINI_API_KEY，
其次来自配置。stream.py 发起 SDK 请求，将流式响应整理为内核模型事件，
保留原始 Part、函数调用、思考签名和用量信息。
model.py 的 GeminiModel 实现 ModelGateway，供 bootstrap 注入 AgentRunner。

调用方向为 kernel.model -> 注入的模型端口 -> 本包 -> Gemini SDK。
本包处理供应商细节，不管理任务计划、工具权限或 CLI 展示；
流和客户端的关闭由运行生命周期显式驱动。
"""
