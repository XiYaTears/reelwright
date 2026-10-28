# AC-02 缺关键信息

- 模式：`offline`，但输入校验失败，未调用模型
- 结果：`status=needs_input`，退出码 3
- `needs_input.missing_fields`：['audience']
- trace 仅 `run_start` / `input_validation` / `run_end` 三个事件，无 `model_response`

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：needs_input
