# AC-03a 素材检索持续无结果（离线）

- 模式：`offline`
- 结果：`status=needs_input`，退出码 3；无脚本/引用
- 实际查询序列：['火星旅拍攻略', '火星', '星旅', '旅拍', '星河旅拍指南']
- 检索观察：[0, 0, 0, 0, 0]（全部 0）
- 行为：改写查询仍无结果后 `ask_user`，未编造素材事实

> 证据来源：本目录 `output.offline.json` 与 `trace.offline.jsonl` 由上方命令实际运行生成，未手写/补写；`mode=offline` 只证明流程与硬约束，不证明真实模型决策质量。
> 复跑退出码：needs_input
