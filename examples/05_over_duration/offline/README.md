# AC-05 超时长修正（离线确定性核心证据）

- 模式：`offline`（固定策略）
- 结果：`status=completed`，退出码 0
- 首稿估算：38s，`verdict=too_long`（目标 30s，区间 {'min_s': 27, 'max_s': 33}）
- 改稿后重新估算：28s，`within=True`
- 提交次数：2；最终总时长：28s
- 最终引用：[{"material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕", "shot_indexes": [3]}]
- 参考实例要求：首稿 >30s、最终 ≤30s；本离线证据可确定性复跑 38→28

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：completed
