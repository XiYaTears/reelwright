# AC-03b 改写查询后命中（离线）

- 模式：`offline`
- 结果：`status=completed`，退出码 0
- 实际查询序列：['怎么加字幕', '加字', '字幕']
- 最终引用：[{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕", "shot_indexes": [3]}]

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：completed
