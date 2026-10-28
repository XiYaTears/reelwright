# AC-05 real：加分证据（不作唯一依赖）

- 模式：`real`；命令：`command.real.txt`
- 结果：`status=completed`，退出码 0；最终总时长 31s（区间 {'min_s': 27, 'max_s': 33}）
- 估算序列（总时长, verdict, within）：[(31, 'within', True)]
- 本次真实模型首稿即落在通用区间（within），**压缩分支未触发**；按 01 AC-05 口径仅用通用区间判定，如实记录。
- 引用：['P01', 'P06', 'P02']；`tool_errors=0`。
- trace 有 1 步 `decision_note_source=missing`，该步证据由编排层 `decision_summary`（source=orchestrator）补足；response_id 均非空。
- 离线 38→28 的确定性核心证据见 `../offline/`。
