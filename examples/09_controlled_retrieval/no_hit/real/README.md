# AC-08b no_hit real：正式决策证据（无素材 → 请求补充）

- 模式：`real`；命令见 `command.real.txt`（`--data-dir ../no_hit_data`）
- 结果：`status=needs_input`，退出码 3；`script=null`
- 查询序列（每步均 0 结果）：['字幕功能', '字幕', '自动识别', '剪辑', '语音转文字', '文字', '视频编辑', '新功能', '转写']
- `needs_input.missing_fields`：['产品/功能官方名称', '功能卖点与参数原文（可引用的素材或文档）', '素材库中对应的 material_id / fact_id']
- 与 `../hit/real` 对照：命中组 completed、无素材组 needs_input，动作随检索结果改变；未编造素材。
