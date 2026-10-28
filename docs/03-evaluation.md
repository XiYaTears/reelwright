# 03 评估

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-28 |
| 状态 | 已定稿（需求方复核通过） |
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
| `pytest -q` | `215 passed, 3 deselected` |
| `pytest -q -m real` | `3 passed, 215 deselected` |

离线测试覆盖：dataset/script/duration/rules/materials/registry/deepseek/mock/state/trace/prompt/config、
LangGraph 全链路（正常、AC-05 38→28、AC-03a/b、AC-04a/b、AC-06、AC-07、AC-08a/b、CITE-001 兜底、
步数/工具预算边界、decision_note/summary 来源）、CLI 端到端（退出码/输入 JSON 合并/注入配置）。
真实测试覆盖：AC-01 正常生成硬约束、无素材数据集 → `needs_input`、注入 timeout 后可恢复。
证据测试对**全部离线 command** 在临时目录逐条复跑，核对 status/退出码/总时长/步数/工具调用数/
注入标记/`needs_input` 与归档 output 一致；所有 `model_response` 均要求 `response_id` 非空
（真实模式为 DeepSeek 响应 ID，离线为合成 ID）。

## 3. 验收场景结果

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| AC-01 正常生成 | offline `completed` 28s；real `completed` 30s，6 镜，4 条素材引用，规则 0 error | `examples/01_normal/output.offline.json`、`trace.offline.jsonl`、`output.real.json`、`trace.real.jsonl` |
| AC-02 缺关键信息 | `needs_input`，缺 `audience`，无 `model_response` | `examples/02_missing_input/output.offline.json`、`trace.offline.jsonl` |
| AC-03a 无结果 | offline：改写+回退共 5 次 0 结果 → `needs_input`；real：改写后命中通用素材 → `completed`（按 AC-03b 分支，已在 README 注明） | `examples/03_no_result/a_no_hit/output.offline.json`、`trace.offline.jsonl`；real：`a_no_hit/real/trace.real.jsonl` |
| AC-03b 改写命中 | offline `completed` 28s，引用 P01 | `examples/03_no_result/b_rewrite_hit/output.offline.json`、`trace.offline.jsonl` |
| AC-04a 工具可恢复失败 | offline/real `completed`，`tool_errors` 含 `injected=true` 的 timeout | `examples/04_tool_failure/a_recoverable/trace.offline.jsonl`；real：`a_recoverable/real/trace.real.jsonl` |
| AC-04b 工具持续失败 | `failed`，`failure.code=tool_failure_unrecovered`，无编造脚本 | `examples/04_tool_failure/b_persistent/output.offline.json`、`trace.offline.jsonl` |
| AC-05 超时长修正 | offline 核心 38→28（≥2 次估算、改稿重估、引用 P01）；real 31s（首稿即 within，压缩分支未触发，为加分证据） | `examples/05_over_duration/offline/trace.offline.jsonl`、`output.offline.json`；real：`05_over_duration/real/trace.real.jsonl` |
| AC-06 无法满足 | `failed`，`cannot_satisfy`；证据含目标区间 [27,33] vs kuaishou 上限 25、`PLAT-kuaishou-DUR-001` | `examples/06_unsatisfiable/output.offline.json`、`trace.offline.jsonl` |
| AC-07 步数上限 | `incomplete`，`step_limit`，保留 38s 草稿 | `examples/07_step_limit/output.offline.json`、`trace.offline.jsonl` |
| AC-08a 只改目标时长 | offline：同 38s 初稿 → 30s 目标压缩 28 / 60s 目标扩张 58；两条 trace 动作方向相反 | `examples/08_controlled_target/target_30/trace.offline.jsonl`、`target_60/trace.offline.jsonl` |
| AC-08b 只改检索结果 | offline/real：hit `completed`；no_hit `needs_input`（查询全部 0 结果）；查询词/动作序列不同 | `examples/09_controlled_retrieval/hit/trace.offline.jsonl`、`no_hit/trace.offline.jsonl`；real：`hit/real/trace.real.jsonl`、`no_hit/real/trace.real.jsonl` |

### 3.1 两条真实执行路径（必需证据，可直接在 trace 里核对）

| 路径 | trace 文件 | 可看到什么 |
| --- | --- | --- |
| 工具失败后调整（AC-04a real） | `examples/04_tool_failure/a_recoverable/real/trace.real.jsonl` | 第 1 次 `search_materials` 返回 `ok=false / injected=true / code=timeout`；随后模型再次检索并继续 `submit_script` → `estimate_duration` / `check_rules` → `finish`。每步含工具名、参数、观察摘要、下一步动作，以及 `decision_note`（模型可见输出）或 `decision_summary`（编排层事实摘要，source=orchestrator） |
| 检索无结果后调整（AC-08b no_hit real） | `examples/09_controlled_retrieval/no_hit/real/trace.real.jsonl` | 多次 `search_materials` 全部 0 条、查询词逐次改写，模型最终调用 `ask_user` → `needs_input`；与 `hit/real/trace.real.jsonl`（同样输入、仅数据集不同）的动作序列明显不同 |

补充一条同向证据：`examples/03_no_result/a_no_hit/real/trace.real.jsonl`（模型改写查询后命中通用素材，实际走 AC-03b 分支）。
步数/工具调用上限证据：`examples/07_step_limit/output.offline.json`（`status=incomplete`、`failure.code=step_limit`、保留 38s 草稿）；批次超预算的 `skipped_tool_calls` 行为由单元测试覆盖。

以上记录只含工具动作、参数、返回摘要与可见说明，不含模型内部思维过程（`reasoning_content` 在客户端即被丢弃）。
`AC-05 real`、`AC-08a real`（未跑）只作加分证据。

## 4. 关键数字（实际运行）

- 离线固定策略：AC-05 首稿 38s（`too_long`，区间 [27,33]）→ 28s（`within`）；AC-08a 60s 目标 38→58s。
- 真实模型：AC-01 30s/6 镜/4 条素材引用；AC-03a real 37→32s；AC-05 real 首稿 31s（`within`，压缩分支未触发）；
  AC-08b no_hit real 连续 6 次 0 结果后 `needs_input`；AC-04a real 注入 timeout 后仍 `completed` 并记录
  injected 错误（另有一次 submit 参数非法由模型自行纠正，如实保留）。
- 真实 trace 的 `model_response.response_id` 均非空（DeepSeek 响应 ID），可人工交叉核对；除 AC-05 real
  有 1 步 `decision_note_source=missing` 外，其余真实步骤均为 `model`，缺失步骤由编排层
  `decision_summary`（source=orchestrator）补足。
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
| 工具预算预检跳过含 `finish` 的批时未清 terminal，存在误判 `completed` 风险 | 独立评审 | 跳过批清空 terminal 并强制走预算收尾；补"跳过批含 finish → incomplete"测试 |
| LLMError 退出码文档（内部错误/1）与实现（failed/5）不一致 | 独立评审 | 统一为 LLMError → failed/5；配置/数据集/未捕获异常 → 1；01/02/README/测试同步 |
| 证据门禁只校验首尾事件、离线 command 未全量复跑；真实 trace 无 API 侧标识 | 独立评审 | 证据测试复跑全部离线 command 并逐项比对；`model_response` 增加 `response_id`；真实性由人工复跑记录 |
| trace 字段缺口：`input_validation` 未写空字段、内部错误 `run_end` 缺 `exit_code`、异常 finalize 缺字段 | 独立评审 | 已补齐并补测试 |
| §5.3 与 §8.4 对"末步 no_action + 触顶"表述矛盾 | 独立评审 | 统一为"编排层强制收尾才 incomplete；主动结束且复算通过可 completed"；补末步 no_action 测试 |

## 6. 限制与未覆盖

- 真实模型输出非确定；AC-05 real 本次首稿 31s 即 `within`（压缩分支未触发），与离线参考 38→28 不同属正常，未改写证据。
- AC-08a 的真实模型对照未跑（非必需证据，仅作加分项），仅有离线确定性对照；如需补齐需重跑并更新本文件。
- AC-03a 真实模型改写为可命中查询（AC-03b 分支），持续无结果的真实证据由 AC-08b no_hit 承担。
- 事实检查不能识别语义级张冠李戴；素材/品牌/平台规则均为合成数据；时长公式为近似值。
- mock 只证明流程，不证明模型决策质量；离线 `decision_summary` 为编排层摘要，不是模型输出（见 04）。
