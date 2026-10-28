# 04 AI 使用记录（初稿，仅列事实）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-28 |
| 状态 | 开发方初稿，待需求方改写定稿 |
| 说明 | 本文只列事实，不美化；需求方负责最终定稿 |

## 1. 使用的 AI 与工具

- 编码 Agent：DeepSeek Harness 中的 `deepseek-flash`（本轮会话），负责代码、测试、文档与证据生成。
- 被开发系统调用的模型：DeepSeek `deepseek-flash`（真实模式，OpenAI 风格 tool calling）；离线模式使用固定策略
  mock，不调用任何模型。
- 依赖：LangGraph `1.2.18`、langchain-core `1.7.0`、httpx `0.28.1`、python-dotenv `1.2.4`、pytest `9.2.0`。
- 未使用：外部 RAG、网络搜索、其他 LLM、托管观测（LangSmith 等）。

## 2. 人工（需求方）做出的决策

- 维护 `AGENTS.md` 工程契约与技术栈批准；提出业务硬约束（事实可追溯、时长、规则、状态、证据规范）。
- 第一轮评审：通过设计，拍板 Q1 ±10% 容差、Q2 四平台与规则范围、Q3 引用口径；提出 9 项修订。
- 盲审：提出 8 条意见（空素材夹具、mock 查询序列、预算 off-by-one、平台时长 rule_id、文档口径、证据测试、
  runner 顺序、LangGraph 归属）；纠正 2 条误判。
- 独立复跑 9 个离线场景并审查 HEAD 提交自洽性，发现 `ToolCall.raw_arguments` 字段未提交。
- 独立验证 `DeepSeekClient` 真机崩溃（`NO_PROXY` 含 `[::1]`）并给出修法；复跑修复后的真机调用。
- 需求方保留：`docs/04–05` 改写定稿、`README.md` 终审、推送远端。

## 3. AI 生成并人工验证的内容

- `src/` 全部代码、`data/` 合成数据、`tests/` 全部测试、`docs/01–03` 与本文初稿、`examples/` 运行证据。
- 验证方式：`pytest -q`（195 passed）、`pytest -q -m real`（3 passed）、按 `examples/*/command.*.txt`
  复跑、检查 trace 字段与引用可回溯性、密钥泄漏扫描（除 `.env.local` 自身外 0 命中）。
- 真机数字：AC-01 `completed` 27s；AC-05 real 36→30；AC-08b no_hit 9 次 0 结果后 `needs_input`；
  AC-04a 注入 timeout 后 `completed` 且 `tool_errors` 有 injected 记录。

## 4. AI 出错与被纠正的事实（全部已修复/记录）

| 问题 | 发现者 | 处理 |
| --- | --- | --- |
| 真机 httpx `trust_env=True` 被 `NO_PROXY=[::1]` 触发崩溃 | 需求方 | `trust_env=False` + `LLM_PROXY`，补回归测试 |
| 真实 API HTTP 422：tools 缺 `type/function` 包装 | AI 真机首跑 | 请求体规范化包装，补测试 |
| `ToolCall.raw_arguments` 使用处已提交、字段未提交，HEAD 不自洽 | 需求方 | 补 `fix` 提交，复验工作区 |
| 控制动作参数非法时仍提前收尾（与 02 §6.5 矛盾） | 需求方盲审 + 测试 | 修复为回循环重试，补测试 |
| `check_rules` 业务违规被误记为工具失败 | AI 证据复核 | 区分工具失败/业务未通过，补测试 |
| AC-03a ask_user `missing_fields` 用 `topic` 语义别扭 | 需求方 | 改为 `materials` + reason"素材库未覆盖该主题" |
| 异常分支 `run_end.steps_used=0` 不真实 | 需求方 | 从已落盘 trace 取实际步数/工具调用数 |
| 第一轮文档误写 `NO_PROXY`/合同措辞/证据默认 skip 等 | 需求方评审 | 已按评审意见修订文档 |

## 5. AI 不能声称/不能替代的部分

- mock 结果不是模型能力证据；离线证据只证明流程与硬约束。
- 真实模型的决策质量不由本项目的自动测试证明；`examples` 真实 trace 为实际运行，数值与分支差异如实记录。
- 事实的语义级正确性（有引用但张冠李戴）无法由现有自动检查保证，依赖人工评审。
- 平台/品牌规则为合成数据，不能作为真实合规依据。
- `decision_summary` 是编排层按实际观察+实际调用拼出的摘要（source=orchestrator），不是模型输出；
  模型可见的 `decision_note` 可能缺失（`source=missing`），两者在 03 与 examples README 中分开说明。
