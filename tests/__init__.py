"""项目测试包：从单模块到完整协作验证架构行为，不参与生产运行。

unit/ 验证单模块、工具和内核依赖边界；integration/ 验证运行循环、
审批、协作与安全协调；external/ 验证真实命令、网络和 Gemini 环境。
conftest.py 按目录添加测试标记，并默认跳过 external，需 --run-external 显式启用。

support.py 提供测试专用的模型/工具装配辅助，方便使用伪实现覆盖运行路径；
它不是生产默认实现，生产对象仍由 agent_service.bootstrap 显式装配。
测试目录划分与运行方式见 tests/README.md。
"""
