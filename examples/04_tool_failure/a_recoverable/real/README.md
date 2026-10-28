# AC-04a real：正式决策证据（注入失败 → 重试/调整）

- 模式：`real`；命令：`command.real.txt`（注入 `search_materials=timeout@1`）
- 结果：`status=completed`，退出码 0；最终总时长 30s（首稿 34s → 最终 `within`）
- `tool_errors`：[('search_materials', 'timeout', True), ('submit_script', 'invalid_arguments', False)]
- trace 中该检索先失败后成功；另有一次 submit 参数非法由模型自行纠正，均如实保留；未把失败当成功。
