# AC-08a 受控对照：目标 30s（离线）

- 与 `../target_60` 除 `target_duration_s` 外逐字段相同
- 首稿 38s → `too_long` → 最终 28s（within=True）

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：completed
