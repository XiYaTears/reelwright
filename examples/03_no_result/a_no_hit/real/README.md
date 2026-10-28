# AC-03a real：真实模型决策证据（实际分支如实记录）

- 模式：`real`（真实 DeepSeek 调用）；命令见 `command.real.txt`
- 结果：`status=completed`，退出码 0；最终总时长 30s
- 查询序列：['火星旅拍', '旅拍 剪辑', '火星', '模板', '字幕', '音乐', '导出', '转场']
- 引用素材：['P06', 'P02', 'P01', 'P08']
- 事实说明：真实模型对"火星旅拍攻略"先多次 0 结果检索，随后改写成通用剪辑关键词并命中素材，按 01 AC-03a 注记判为 `completed`（AC-03b 分支）；**正式"持续无结果 → needs_input"决策证据见 `examples/09_controlled_retrieval/no_hit/real/`**。
- 本目录 trace 来自实际运行，未补写；真实模型行为与离线固定策略不同属正常。
