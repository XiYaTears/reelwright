# AC-04a real：正式决策证据（注入失败 → 重试/调整）

- 模式：`real`；命令见 `command.real.txt`（注入 `search_materials=timeout@1`）
- 结果：`status=completed`，退出码 0
- `tool_errors`：[('search_materials', 'timeout', True)]
- trace 中该工具先失败后成功，模型继续完成；未把失败当成功。
