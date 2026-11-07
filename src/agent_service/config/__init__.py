"""配置与提示词资源：保存运行默认值，不负责装配或执行任务。

settings.py 定义并校验模型、输出审核等配置，在模块导入时读取 config.yaml、
system_prompt.md 和 safety_prompt.md，形成 settings 单例。
修改这些文件后需重启进程；仅导入本包不会触发 settings 的加载。

bootstrap 及外部适配器读取配置，再把所需值注入内核；
kernel 不应直接导入 settings。API key 的环境变量优先级与客户端创建
由 adapters.gemini.client 处理，不应在包初始化时发起模型请求。
模型名称以 config.yaml 为准，提示词文件是随包分发的资源。
"""
