# AC-04a 工具失败可恢复（离线）

- 注入：`search_materials=timeout@1`
- 结果：`status=completed`，退出码 0
- `tool_errors`：[{"tool": "search_materials", "code": "timeout", "message": "注入故障：search_materials 第 1 次调用失败（timeout）", "call_index": 1, "injected": true, "step": 1, "retryable": true}]
- trace 中该工具先 `ok=false` 后成功；最终引用仍来自真实素材

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：completed
