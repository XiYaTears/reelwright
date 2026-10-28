# AC-04b 工具持续失败（离线）

- 注入：`search_materials=unavailable`（每次调用失败）
- 结果：`status=failed`，退出码 5
- `failure`：{"code": "tool_failure_unrecovered", "message": "没有可复算的提交稿", "evidence": {"tool_error_count": 2}}
- `tool_errors`：[{"tool": "search_materials", "code": "unavailable", "message": "注入故障：search_materials 第 1 次调用失败（unavailable）", "call_index": 1, "injected": true, "step": 1, "retryable": true}, {"tool": "search_materials", "code": "unavailable", "message": "注入故障：search_materials 第 2 次调用失败（unavailable）", "call_index": 2, "injected": true, "step": 2, "retryable": true}]
- 未假装成功：没有脚本、没有编造素材引用

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：failed
