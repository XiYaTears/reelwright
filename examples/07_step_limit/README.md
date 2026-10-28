# AC-07 步数上限（状态覆盖）

- `--max-steps 3`，模型未能收尾
- 结果：`status=incomplete`，退出码 4
- `budget.steps_used`：3
- `failure.code`：step_limit

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：incomplete
