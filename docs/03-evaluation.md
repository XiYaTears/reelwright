# 03 评估

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-28 |
| 状态 | 第二轮实现后初评（待需求方复核） |
| 运行环境 | Windows，Python 3.13.5，项目内 `.venv`，`PYTHONUTF8=1` |
| 证据目录 | `examples/`（每组含 input/command/output/trace/README） |

## 1. 评估方法

- 离线：`pytest -q`（无网络、无密钥；含 examples 证据结构一致性测试），见 `examples/pytest-offline.txt`。
- 真实模式：`pytest -q -m real`（3 个冒烟测试，真实 DeepSeek 调用），见 `examples/pytest-real.txt`。
- 场景级：按 `examples/<场景>/command.*.txt` 逐条复跑，对照 `output.*.json` 与 `trace.*.jsonl`；
  trace 来自实际运行，禁止手写/补写（约束见 `examples/README.md`、`docs/02-solution.md` §15.4.4）。
- 硬约束判定以 finalize 独立复算结果为准（不信任模型提交的工具结果）。

## 2. 测试结果（实际）

| 命令 | 结果 |
| --- | --- |
| `pytest -q` | `198 passed, 3 deselected` |
| `pytest -q -m real` | `3 passed, 198 deselected` |

离线测试覆盖：dataset/script/duration/rules/materials/registry/deepseek/mock/state/trace/prompt/config、
LangGraph 全链路（正常、AC-05 38→28、AC-03a/b、AC-04a/b、AC-06、AC-07、AC-08a/b、CITE-001 兜底、
步数/工具预算边界、decision_note/summary 来源）、CLI 端到端（退出码/输入 JSON 合并/注入配置）。
真实测试覆盖：AC-01 正常生成硬约束、无素材数据集 → `needs_input`、注入 timeout 后可恢复。

## 3. 验收场景结果

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| AC-01 正常生成 | offline `completed` 28s；real `completed` 27s，6 镜，3 条素材引用 | `examples/01_normal/`（`output.offline.json`、`output.real.json`） |
| AC-02 缺关键信息 | `needs_input`，缺 `audience`，无 `model_response` | `examples/02_missing_input/` |
| AC-03a 无结果 | offline：改写+回退共 5 次 0 结果 → `needs_input`；real：改写后命中通用素材 → `completed`（按 AC-03b 分支，已在 README 注明） | `examples/03_no_result/a_no_hit/`（offline + real） |
| AC-03b 改写命中 | offline `completed` 28s，引用 P01 | `examples/03_no_result/b_rewrite_hit/` |
| AC-04a 工具可恢复失败 | offline/real `completed`，`tool_errors` 含 `injected=true` 的 timeout | `examples/04_tool_failure/a_recoverable/` |
| AC-04b 工具持续失败 | `failed`，`failure.code=tool_failure_unrecovered`，无编造脚本 | `examples/04_tool_failure/b_persistent/` |
| AC-05 超时长修正 | offline 核心 38→28（≥2 次估算、改稿重估、引用 P01）；real 36→30（`completed`，为加分证据） | `examples/05_over_duration/` |
| AC-06 无法满足 | `failed`，`cannot_satisfy`；证据含目标区间 [27,33] vs kuaishou 上限 25、`PLAT-kuaishou-DUR-001` | `examples/06_unsatisfiable/` |
| AC-07 步数上限 | `incomplete`，`step_limit`，保留 38s 草稿 | `examples/07_step_limit/` |
| AC-08a 只改目标时长 | offline：同 38s 初稿 → 30s 目标压缩 28 / 60s 目标扩张 58；两条 trace 动作方向相反 | `examples/08_controlled_target/` |
| AC-08b 只改检索结果 | offline/real：hit `completed`；no_hit `needs_input`（查询全部 0 结果）；查询词/动作序列不同 | `examples/09_controlled_retrieval/` |

正式真实决策证据（刺激必然发生）：`AC-03a real`、`AC-08b no_hit real`、`AC-04a real`。
`AC-05 real`、`AC-08a real`（未跑）只作加分证据。

## 4. 关键数字（实际运行）

- 离线固定策略：AC-05 首稿 38s（`too_long`，区间 [27,33]）→ 28s（`within`）；AC-08a 60s 目标 38→58s。
- 真实模型：AC-01 27s/6 镜/3 素材引用；AC-05 36s（`too_long`）→ 30s（`within`）；
  AC-08b no_hit 连续 9 次 0 结果后 `needs_input`；AC-04a 注入 timeout 后仍 `completed` 并记录 injected 错误。
- 全部 `completed` 输出均满足：逐镜时长与公式复算一致、总时长等于逐镜和、引用原文逐字可回溯、
  最终规则检查 0 error。

## 5. 评估中发现并修复的缺陷

| 缺陷 | 发现方式 | 修复 |
| --- | --- | --- |
| `DeepSeekClient` 构造崩溃（`NO_PROXY` 含 `[::1]`，httpx `trust_env=True`） | 真机调用 | `trust_env=False` + 显式 `LLM_PROXY`；回归测试 |
| 真实请求 `tools` 未包 `{"type":"function",...}` → HTTP 422 | 真实 AC-01 首次运行 | 请求体规范化包装 + 测试 |
| `ToolCall.raw_arguments` 使用处已提交、字段未提交（HEAD 不自洽） | 需求方独立审查 | 补提交 `fix: ToolCall 保留原始 arguments 字符串` |
| `finish/ask_user` 参数非法仍提前 finalize | 评审 + 单测 | 工具节点校验失败清空 terminal 回循环；补测试 |
| `check_rules` 业务违规（`ok:false`）被误记为工具失败 | 离线 examples 审查 | 区分"工具执行失败"与"业务未通过"；补测试 |
| 示例 `command.*.txt` 多写一个 `run`，19 个中 18 个不能直接复跑 | 需求方复跑 | 重新生成 19 个命令文件；新增 `tests/test_command_files.py` 用真实 argparse 解析拦截 |

## 6. 限制与未覆盖

- 真实模型输出非确定；AC-05 real 数值（36→30）与离线参考（38→28）不同属正常，未改写证据。
- AC-08a 的真实模型对照未跑（非必需证据，仅作加分项），仅有离线确定性对照；如需补齐需重跑并更新本文件。
- AC-03a 真实模型改写为可命中查询（AC-03b 分支），持续无结果的真实证据由 AC-08b no_hit 承担。
- 事实检查不能识别语义级张冠李戴；素材/品牌/平台规则均为合成数据；时长公式为近似值。
- mock 只证明流程，不证明模型决策质量；离线 `decision_summary` 为编排层摘要，不是模型输出（见 04）。
