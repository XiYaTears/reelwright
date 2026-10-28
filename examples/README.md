# examples 证据目录

所有 trace/输出均由本仓库 CLI 实际运行生成（`--offline` 会明确标注 `mode=offline`），
禁止手写、补写、编辑或跨运行拼接；复跑命令见各目录 `command.*.txt`。

- 离线证据只证明流程与硬约束，不证明真实模型决策质量；真实模型证据以 `*.real.json` /
  `*.real.jsonl` 后缀区分，且必须来自真实 DeepSeek 调用。
- trace 两列来源：`decision_note` / `decision_note_source` 来自模型可见 content（可能
  `missing`）；`decision_summary` / `decision_summary_source=orchestrator` 是编排层按
  "上一步实际观察 + 本步实际工具名/参数"拼出的事实摘要，不是模型输出。
- 正式真实决策证据：AC-03a / AC-08b no_hit / AC-04a（刺激必然发生）；AC-05 real、
  AC-08a real 只作加分证据。
- 测试结果：`pytest-offline.txt`（全量离线）、`pytest-real.txt`（真实冒烟）。

| 目录 | 场景 |
| --- | --- |
| `01_normal/` | AC-01 正常生成 |
| `02_missing_input/` | AC-02 缺关键信息 |
| `03_no_result/` | AC-03 检索无结果（a 持续无结果 / b 改写命中） |
| `04_tool_failure/` | AC-04 工具失败（a 可恢复 / b 持续失败） |
| `05_over_duration/` | AC-05 超时长修正（offline 核心 38→28；real 为加分证据） |
| `06_unsatisfiable/` | AC-06 无法满足 |
| `07_step_limit/` | AC-07 步数上限 |
| `08_controlled_target/` | AC-08a 只改目标时长 |
| `09_controlled_retrieval/` | AC-08b 只改检索结果 |
