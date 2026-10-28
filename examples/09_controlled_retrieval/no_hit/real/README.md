# AC-08b no_hit real：正式决策证据（无素材 → 请求补充）

- 模式：`real`；命令：`command.real.txt`（`--data-dir ../no_hit_data`）
- 结果：`status=needs_input`，退出码 3；`script=null`
- 查询序列（每步均 0 结果）：['字幕功能', '自动字幕', '字幕', '剪辑', '视频', '文字']
- `needs_input.missing_fields`：['可引用的字幕功能事实原文（素材ID/标题）', '核心卖点或功能点清单', '功能可用范围（平台/版本/语言）', '操作步骤要点']
- 与 `../hit/real` 对照：命中组 completed、无素材组 needs_input，动作随检索结果改变；未编造素材；response_id 均非空。
