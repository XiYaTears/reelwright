# examples 证据目录

所有 trace/输出均由本仓库 CLI 实际运行生成（`--offline` 标注 `mode=offline`，真实模型标注
`mode=real`），禁止手写、补写、编辑或跨运行拼接；复跑命令见各目录 `command.*.txt`。

## 来源与限制

- 离线证据只证明流程与硬约束，不证明真实模型决策质量；真实证据必须来自真实 DeepSeek 调用
  （`run_start.model` 有值、非 mock）。
- trace 两列来源：`decision_note` / `decision_note_source` 来自模型可见 content（可能
  `missing`）；`decision_summary` / `decision_summary_source=orchestrator` 是编排层按
  "上一步实际观察 + 本步实际工具名/参数"拼出的事实摘要，不是模型输出，03/04 已如实说明。
- 正式真实决策证据：`AC-03a real`（多次 0 结果后改写，实际为 AC-03b 分支）、
  `AC-08b no_hit real`（连续 0 结果 → needs_input）、`AC-04a real`（注入 timeout → 重试后完成）。
  `AC-05 real`、`AC-08a real` 只作加分证据。
- 测试结果：`pytest-offline.txt`（全量离线+证据结构测试）、`pytest-real.txt`
  （真实冒烟，3 passed）。
- 离线数字确定性（38→28 / 38→58）来自固定策略；真实数字不同属正常，各 README 如实记录。

| 目录 | 场景 | 已含模式 |
| --- | --- | --- |
| `01_normal/` | AC-01 正常生成 | offline, real |
| `02_missing_input/` | AC-02 缺关键信息 | offline |
| `03_no_result/` | AC-03 检索无结果（a 持续无结果 / b 改写命中） | offline；a 另有 real |
| `04_tool_failure/` | AC-04 工具失败（a 可恢复 / b 持续失败） | offline；a 另有 real |
| `05_over_duration/` | AC-05 超时长修正（离线核心 38→28） | offline, real |
| `06_unsatisfiable/` | AC-06 无法满足 | offline |
| `07_step_limit/` | AC-07 步数上限 | offline |
| `08_controlled_target/` | AC-08a 只改目标时长 | offline |
| `09_controlled_retrieval/` | AC-08b 只改检索结果 | offline, real |
