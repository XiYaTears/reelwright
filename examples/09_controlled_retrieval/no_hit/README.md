# AC-08b 受控对照：无素材（离线）

- 数据源：`--data-dir ../no_hit_data`（materials 为空数组，规则同默认数据）
- 结果：`status=needs_input`，退出码 3
- 查询序列：['字幕功能上线', '字幕', '幕功', '功能', '星河旅拍指南']
- 检索结果：[0, 0, 0, 0, 0]（全部 0）
- 无脚本/无引用，未编造

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：needs_input
