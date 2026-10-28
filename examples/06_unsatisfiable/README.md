# AC-06 无法满足（明确失败）

- 平台 `kuaishou` 合成上限 25s，目标 30s（区间 [27,33]），无法同时满足
- 结果：`status=failed`，退出码 5
- `failure.code`：cannot_satisfy
- 规则命中：['PLAT-kuaishou-DUR-001']
- 证据：目标区间下限 27 > 平台上限 25，未伪造 completed

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：failed
