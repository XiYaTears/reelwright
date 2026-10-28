# AC-01 正常生成（离线）

- 模式：`offline`（固定策略 mock）
- 命令：见 `command.offline.txt`
- 结果：`status=completed`，退出码 0
- 总时长：28s（目标 30s，区间 {'min_s': 27, 'max_s': 33}）
- 分镜数：5；引用：[{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕", "shot_indexes": [3]}]
- 规则检查：`ok=True`，errors=0
- trace 关键：`model_response.decision_note_source` 与 `decision_summary_source=orchestrator` 区分来源；
  `decision_summary` 为编排层事实摘要，不是模型输出。

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：completed
