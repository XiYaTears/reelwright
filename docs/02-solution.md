# 02 方案设计

| 项 | 内容 |
| --- | --- |
| 版本 | v1.1（定稿） |
| 日期 | 2026-10-28 |
| 状态 | 已定稿（需求方终审通过，实现与本文一致） |
| 维护 | 开发方（终审与定稿由需求方完成） |
| 上游 | `docs/01-requirements.md`、`AGENTS.md`、两轮评审与终审意见 |
| 说明 | 本文为设计依据；实现已按本文完成并冻结，实现细节以代码与 `docs/03` 为准 |

## 1. 设计目标与原则

1. **硬约束在编排层兜底**：模型可以自由决策，但事实引用、时长、规则、状态由工具与收尾节点独立复算，模型自称"完成"不算数。
2. **工具是纯函数**：三个业务工具不依赖模型、不联网、不随机；同一输入必得同一输出，便于测试和"改稿后重算"。
3. **一切可观测**：每次模型调用、每次工具调用、最终校验都落 trace；不记录 `reasoning_content` 或模型内部思维。
4. **显式编排**：不用 `create_react_agent` 黑盒，用 `StateGraph` 显式节点 + 条件边，终止条件可判定、预算可强制。
5. **模型行为与流程解耦**：真实 LLM 与离线 mock 同一接口；mock 只证明链路，不证明决策质量。
6. **不过度抽象**：单进程、单脚本、单用户；模块只在被真实调用处存在，不预留"未来扩展"接口。

## 2. 系统总览

```
CLI (argparse / --input JSON)
 └─ runner：环境/配置与数据集 fail-fast → 创建 TraceWriter → 输入校验（needs_input 也必须有 run_start/input_validation/run_end）
      → StateGraph.invoke(initial_state)
      ├─ agent 节点 ──> LLMClient（DeepSeek 真实 / Mock 离线）
      │      └─ 返回 content + tool_calls（全部保留，逐条执行）→ trace
      ├─ tools 节点 ──> ToolRegistry（参数校验 + 失败注入 + 顺序执行）
      │      ├─ search_materials   → data/materials.json
      │      ├─ estimate_duration  → 确定性公式
      │      ├─ check_rules        → 品牌/平台规则 + 引用校验
      │      └─ submit_script / ask_user / finish（状态动作，不落业务工具）
      └─ finalize 节点：对最新提交稿独立复算估算 + 规则 → 计算 status
 └─ runner：组装 RunOutput（脚本/引用/风险/状态/失败原因）→ stdout + output.json
```

一次典型运行（正常场景）约 5–7 步：检索（可能含一次改写）→ 提交脚本 → 估算 + 规则检查 → 按结果改稿 → 重估重查 → finish。步数上限默认 8，工具调用上限 `2 × 步数上限 = 16`，均在编排层强制（详见 §5.3）。

## 3. 架构与模块边界

### 3.1 目录与模块

```
src/
  __init__.py
  cli.py                 # argparse、参数/JSON 合并、退出码映射；不含业务逻辑
  config.py              # dotenv 加载（仅真实模式）、运行参数、预算、路径、密钥持有
  dataset.py             # 读取并校验 data/*.json，计算 data_digest；对外只暴露只读对象
  domain/
    __init__.py
    models.py            # dataclass：Material/Fact/Script/Shot/Citation/EstimateResult/Violation/RuleReport/Requirements/RunOutput
    status.py            # Status、ExitCode、失败码常量
  llm/
    __init__.py
    base.py              # LLMClient Protocol、ToolCall/LLMResponse/Usage、LLMError
    deepseek.py          # httpx 实现：OpenAI 风格 /chat/completions
    mock.py              # 离线固定策略实现，同接口
  tools/
    __init__.py
    materials.py         # search_materials：纯函数 search() + 工具包装
    duration.py          # estimate_duration：纯函数 estimate() + 常量 + 工具包装
    rules.py             # check_rules：纯函数 check() + 工具包装
    registry.py          # 工具注册表：JSON Schema、参数校验、失败注入、调用计数、错误观察
  agent/
    __init__.py
    state.py             # RunState TypedDict、预算常量
    prompt.py            # 系统提示词与首条 user 消息
    trace.py             # TraceWriter（JSONL、密钥脱敏、禁止 reasoning_content）
    nodes.py             # agent_node / tool_node / finalize_node 工厂
    graph.py             # build_graph()：StateGraph + 路由函数
    runner.py            # run()：校验、开关模式、invoke、组装输出
data/
  materials.json         # 合成素材（P01 仅"支持字幕"一条事实）
  brand_rules.json       # 品牌禁用词、夸大表述、事实支撑词模式
  platform_rules.json    # 平台禁用词、平台总时长上限
tests/                   # 单元 + 离线集成 + CLI 端到端
examples/                # 验收证据（已落盘）
docs/
```

### 3.2 模块职责与依赖

| 模块 | 职责 | 不允许做 | 依赖方向 |
| --- | --- | --- | --- |
| `cli` | 解析参数/JSON、选模式与路径、打印 JSON、映射退出码 | 读写数据集、拼 prompt、跑图 | → `config`、`agent.runner` |
| `config` | 加载 `.env.local`（仅真实模式）、校验环境变量、持有预算/路径参数 | 把密钥写日志或 state | 无业务依赖 |
| `dataset` | 加载/校验三个 JSON、提供只读数据、算 digest | 业务判定 | 标准库 |
| `domain` | 纯数据类型与枚举 | I/O、模型调用 | 无 |
| `llm.base` | 统一模型接口与响应结构 | 具体 HTTP | 无 |
| `llm.deepseek` | DeepSeek HTTP、`tool_calls` 解析、`reasoning_content` 丢弃、重试 | 编排决策 | → `llm.base` |
| `llm.mock` | 固定策略产生与真实模型同形的响应 | 调工具、网络 | → `llm.base`、`dataset`、`domain` |
| `tools.*` | 纯业务计算/检索/检查 | 模型调用、写文件 | → `dataset`、`domain` |
| `tools.registry` | 工具规格、参数校验、失败注入、调度 | 业务判定细节 | → `tools.*` |
| `agent.nodes` | 模型节点/工具节点/收尾节点的状态转换 | 渲染输出 | → `llm.base`、`tools.registry`、`domain` |
| `agent.graph` | 图结构与条件边、递归上限 | 业务逻辑 | → `agent.nodes` |
| `agent.runner` | 输入校验、模式装配、invoke、输出组装、退出码 | 模型决策 | → 全部下层 |
| `agent.trace` | 事件落盘、脱敏、schema 校验 | 获取密钥以外的敏感信息 | 无 |

依赖单向、无环；`domain` 与 `llm.base` 为最底层，可被测试单独导入。

### 3.3 运行期对象装配

- `runner` 读取 `Requirements`；**先做环境/配置与数据集 fail-fast（缺配置/数据集不可用 → 内部错误退出 1），再创建 `TraceWriter`，然后做输入校验**（`needs_input` 场景也要留下 `run_start` / `input_validation` / `run_end`）。
- 三个业务工具以"闭包绑定运行上下文"的方式注册：`search_materials(dataset)`、`estimate_duration(target_duration_s)`、`check_rules(dataset, platform)`。**目标时长与平台来自输入，不由模型传参**，模型无法通过工具参数篡改判定基准。
- `agent`/`tools`/`finalize` 节点由 `nodes.py` 工厂接收 `(llm, registry, dataset, requirements, trace, budgets)` 后闭包生成；`RunState` 只放决策相关数据，trace/配置不进 state。

### 3.4 CLI 与实验开关

```text
# 输入 JSON（推荐用于验收/证据）
python -m src.cli run --input examples/01_normal/input.json --out ... --trace ...

# CLI 参数（不做交互式输入）
python -m src.cli run --topic "字幕功能上线" --audience "剪辑新手" --platform douyin \
    --target-duration 30 --style "轻快教程" [--notes "..."] [--offline]

# 通用可选参数
--offline                          # 离线 mock 模式；缺省真实模式
--out PATH --trace PATH            # 覆盖默认 runs/<run_id>/ 输出位置
--max-steps N                      # 1–20，默认 8；工具调用上限自动取 2N
--inject-failure TOOL=CODE[@N]     # 可重复；TOOL ∈ 三个业务工具，CODE ∈ timeout|unavailable|invalid_response
--data-dir PATH                    # 数据集目录覆盖，默认 data/；仅用于受控实验/测试
```

- 输入 JSON 字段与 CLI 参数映射：`target_duration_s` ↔ `--target-duration`，`extra_notes` ↔ `--notes`；同名字段 CLI 覆盖 JSON。
- `--data-dir` 是受控对照实验的唯一数据集开关（例如同一主题对比"有素材/无素材"）：目录内三个 JSON 必须齐全并通过校验，`data_digest` 写入 trace 与输出，保证实验可追溯；不传时使用默认 `data/`。
- 默认输出 `runs/<run_id>/output.json` 与 `runs/<run_id>/trace.jsonl`；验收证据复制到 `examples/` 并由目录 README 标注 mode 与命令。
- 两个输入通道都提供 `failure_injection`（JSON）与 `--inject-failure`（CLI），合并后同一工具只允许一条规则，重复即用法错误（退出 2）。

## 4. 数据模型

### 4.1 素材（`data/materials.json`）

```json
{
  "version": "materials-v1",
  "materials": [
    {
      "material_id": "P01",
      "product": "小影",
      "title": "字幕功能说明",
      "tags": ["字幕", "剪辑"],
      "facts": [
        { "fact_id": "P01-F1", "text": "支持字幕", "kind": "feature" }
      ],
      "source": "虚构产品资料 2026-01"
    }
  ]
}
```

约束（`dataset.py` 启动即校验，错误直接明确报错退出）：

- `material_id` 全库唯一，格式 `P\\d{2}`；`fact_id` 全库唯一，格式 `<material_id>-F\\d`。
- 每条事实 `text` 非空、≤120 字、无首尾空白；`kind ∈ {feature, parameter, scene, audience, certification}`。
- 素材标题 ≤60 字；标签 1–6 个；`P01` 固定只含一条事实"支持字幕"（验收参考实例）。
- 全部合成虚构数据，禁止真实品牌/产品/认证；素材中**不出现任何效果数据**（如"效率提升 80%"），以保证 R2 反例可判定。

### 4.2 规则（`data/brand_rules.json`、`data/platform_rules.json`）

```json
{
  "version": "brand-rules-v1",
  "brand_name": "小影",
  "banned_terms": [
    { "rule_id": "BRAND-001", "term": "最强", "severity": "error", "reason": "绝对化用语", "suggestion": "删除绝对化表述" }
  ],
  "exaggeration_patterns": [
    { "rule_id": "EXAG-001", "pattern": "保证[^，。；\\n]{0,10}(效果|收益)", "severity": "error", "reason": "效果保证类夸大表述", "suggestion": "删除保证/承诺性表述" }
  ],
  "claim_patterns": [
    { "rule_id": "CITE-001", "pattern": "\\d+(?:\\.\\d+)?%", "label": "百分比数据" },
    { "rule_id": "CITE-002", "pattern": "\\d+(?:\\.\\d+)?\\s*(?:倍|元|mAh|GB|MB|W|Hz|fps|K|P)", "label": "参数/金额类数据" },
    { "rule_id": "CITE-003", "pattern": "认证|专利|质检|医用|国家级|独家|首创|唯一|权威|指定", "label": "资质/背书类表述" }
  ]
}
```

```json
{
  "version": "platform-rules-v1",
  "platforms": {
    "douyin":      { "display_name": "抖音",   "max_total_duration_s": 180, "max_duration_rule_id": "PLAT-douyin-DUR-001",      "banned_terms": [] },
    "kuaishou":    { "display_name": "快手",   "max_total_duration_s": 25,  "max_duration_rule_id": "PLAT-kuaishou-DUR-001",    "banned_terms": [] },
    "xiaohongshu": { "display_name": "小红书", "max_total_duration_s": 60,  "max_duration_rule_id": "PLAT-xiaohongshu-DUR-001", "banned_terms": [] },
    "shipinhao":   { "display_name": "视频号", "max_total_duration_s": 120, "max_duration_rule_id": "PLAT-shipinhao-DUR-001",   "banned_terms": [] }
  }
}
```

约束：规则 ID 全局唯一且符合前缀约定（`BRAND-`/`PLAT-<platform>-`/`EXAG-`/`CITE-`/`STRUCT-`）；平台时长上限必须有独立 `max_duration_rule_id`（格式 `PLAT-<platform>-DUR-###`），`check_rules` 命中时使用该 ID；正则必须可编译；平台枚举固定四个，未知平台由输入校验拦截为 `needs_input`。**素材数组允许为空**（用于 AC-08b 空素材夹具），空素材是合法数据集，不是加载错误。所有规则注明为合成近似规范，非法务级依据。**合成说明**：`kuaishou.max_total_duration_s=25` 是内部试用渠道规范（合成），专门用于构造 AC-06"目标区间 [27,33] 与上限 25 互斥"的明确失败场景，不代表真实平台规则；其余平台上限同样为合成值。

### 4.3 脚本模型

**提交稿（`submit_script` 参数，模型唯一能定义脚本的入口）**：

```json
{
  "title": "支持字幕，新手也能剪",
  "shots": [
    {
      "visual": "手机竖屏画面：新手在剪辑界面添加字幕",
      "narration": "支持字幕",
      "caption": "支持字幕",
      "citations": [
        { "material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕" }
      ]
    }
  ]
}
```

约束：`title` 1–60 字；`shots` 1–12 个；每镜 `visual` 1–200 字；`narration` 0–120 字、`caption` 0–60 字，二者至少一个非空；`citations` 0–4 条/镜，每条三元组必填；**提交稿不含任何时长字段**——时长只能由估算工具计算，防止模型自填。

**运行期脚本（内存 dataclass）**：`Script(title, shots[], platform, target_duration_s, style)`，其中 `Shot` 附带 `duration_s`、`duration_breakdown`（由估算结果回填）与 `citations`。

**输出脚本（`output.script`）**：在提交稿基础上，逐镜补 `duration_s`、`duration_breakdown`，新增 `estimated_total_duration_s`、`window`、`citations`（跨镜去重聚合，含 `shot_indexes`）、`risks`。字段定义见 §4.6。

### 4.4 工具结果模型

- **估算结果 `EstimateResult`**：`per_shot[]`、`total_duration_s`、`target_duration_s`、`window{min_s,max_s}`、`within`、`verdict(within|too_long|too_short)`、`over_by_s`、`under_by_s`、`formula_version`。
- **规则结果 `RuleReport`**：`ok`（无 error）、`platform`、`violations[]`、`stats{shots_checked, rules_checked, errors, warnings, truncated}`。
- **违规项 `RuleViolation`**：`rule_id`、`category(brand|platform|exaggeration|citation|structure)`、`severity(error|warning)`、`shot_index(int|null)`、`field(title|narration|caption|null)`、`matched_text`、`message`、`suggestion`。
- **工具错误**（模型看到的观察）：`{"ok": false, "error": {"code": "timeout|unavailable|invalid_response|invalid_arguments|unknown_tool|internal", "message": "...", "retryable": true|false}}`。

### 4.5 图状态（`RunState`）

| 字段 | 类型 | 归约 | 说明 |
| --- | --- | --- | --- |
| `messages` | `list[dict]` | `operator.add` | OpenAI 风格消息；assistant 消息已剥离 `reasoning_content` |
| `step` | `int` | 覆盖 | 已发生的模型调用次数 |
| `tool_calls_used` | `int` | 覆盖 | 已执行的工具调用次数（含失败） |
| `draft` | `dict \| None` | 覆盖 | 最新一次 `submit_script` 的脚本 JSON |
| `draft_revision` | `int` | 覆盖 | 提交次数，从 1 开始 |
| `last_estimate` | `dict \| None` | 覆盖 | 最近一次成功估算结果 |
| `last_rule_report` | `dict \| None` | 覆盖 | 最近一次成功规则检查结果 |
| `estimate_digest` / `rules_digest` | `str \| None` | 覆盖 | 被估算/检查脚本的规范化 sha256 前 16 位，用于发现"检查的不是提交稿" |
| `terminal` | `str \| None` | 覆盖 | `finish` / `ask_user` / `no_action` / `invalid` / `None` |
| `finish_outcome` | `str \| None` | 覆盖 | `completed_intent` / `cannot_satisfy` |
| `pending_question` | `dict \| None` | 覆盖 | `ask_user` 的 `{question, missing_fields, reason}` |
| `status` | `str \| None` | 覆盖 | 收尾节点写入的四状态之一 |
| `failure` | `dict \| None` | 覆盖 | `{code, message, evidence}` |
| `final_checks` | `dict \| None` | 覆盖 | 收尾复算的 `{estimate, rules, engine: "finalize"}` |
| `tool_errors` | `list[dict]` | `operator.add` | 工具错误历史（含注入标记），进输出与风险 |
| `last_observations` | `list[dict] \| None` | 覆盖 | 上一步每个工具调用的观察摘要（`tool/ok/summary`），供编排层生成 `decision_summary`，不用于业务判定 |

trace 写入器、LLM 客户端、数据集、预算等通过闭包进节点，不进 state（避免序列化与 reducer 复杂度）。

### 4.6 运行输出（`RunOutput`，输出 JSON 顶层）

```json
{
  "run_id": "20261028T221500+0800-7f3a9c",
  "mode": "real|offline",
  "status": "completed|needs_input|incomplete|failed",
  "input": {
    "topic": "字幕功能上线", "audience": "剪辑新手", "platform": "douyin",
    "target_duration_s": 30, "style": "轻快教程", "extra_notes": null
  },
  "script": {
    "title": "支持字幕，新手也能剪",
    "platform": "douyin", "style": "轻快教程",
    "target_duration_s": 30,
    "window": { "min_s": 27, "max_s": 33 },
    "shots": [
      {
        "index": 1,
        "visual": "手机竖屏画面：新手在剪辑界面添加字幕",
        "narration": "支持字幕",
        "caption": "支持字幕",
        "duration_s": 6,
        "duration_breakdown": {
          "narration_chars": 5, "caption_chars": 5,
          "narration_s": 2, "caption_s": 1
        },
        "citations": [
          { "material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕" }
        ]
      }
    ],
    "estimated_total_duration_s": 28,
    "citations": [
      { "material_id": "P01", "fact_id": "P01-F1", "quote": "支持字幕", "shot_indexes": [1] }
    ],
    "risks": [
      { "category": "tool", "severity": "warning", "message": "search_materials 曾失败 1 次（timeout，已重试）" }
    ]
  },
  "checks": { "estimate": { "…": "EstimateResult" }, "rules": { "…": "RuleReport" } },
  "needs_input": null,
  "failure": null,
  "tool_errors": [
    { "tool": "search_materials", "code": "timeout", "message": "…", "call_index": 1, "injected": true, "step": 1, "retryable": true }
  ],
  "budget": { "max_steps": 8, "steps_used": 6, "max_tool_calls": 16, "tool_calls_used": 7 },
  "data_digest": "a1b2c3d4e5f6",
  "trace_path": "examples/01_normal/trace.jsonl",
  "generated_at": "2026-10-28T22:15:00+08:00"
}
```

规则：`completed` ⇒ `script` 非空、`checks.estimate.within == true`、`checks.rules.ok == true`、`failure == null`；`needs_input` ⇒ `needs_input != null` 且 `script == null`；`incomplete/failed` 可以有 `script`（作为证据）但不得出现在 `citations` 之外编造内容。`risks` 只放 warning/工具历史/口径提示，error 违规进 `failure.evidence` 或 `checks.rules.violations`。

### 4.7 trace 模型

见 §10。文件：`trace.jsonl`（一行一事件）+ 输出 JSON 中的 `trace_path`。

## 5. Agent 决策循环

### 5.1 图形结构

```
START → agent ──(有 tool_calls 且未到止损)──→ tools ──(未终止且未触顶)──→ agent
            │                                         │
            ├─(无 tool_calls)────────────────────────→ finalize → END
            └─(finish/ask_user 出现在本批)────────────→ tools（执行完整批）→ finalize
```

两个路由函数（纯函数，可单测）：

```python
def route_after_agent(state) -> Literal["tools", "finalize"]:
    if not state["messages"][-1].get("tool_calls"):
        return "finalize"           # 无 action = 模型主动终止，收尾复算
    return "tools"                  # 含 finish/ask_user 也先执行整批，保证逐条回填

def route_after_tools(state) -> Literal["agent", "finalize"]:
    if state["terminal"] in ("finish", "ask_user", "invalid"):
        return "finalize"
    if state["step"] >= MAX_STEPS or state["tool_calls_used"] >= MAX_TOOL_CALLS:
        return "finalize"
    return "agent"
```

> `tools` 节点在**执行前**预检 `tool_calls_used + len(本批) > MAX_TOOL_CALLS`：超限时本批不执行，trace 记录 `skipped_tool_calls`（调用名/id），直接 finalize 为 `incomplete`；不出现"先超发再截断"。

### 5.2 一个模型步的语义

1. **agent 节点**：调 `LLMClient.chat(messages, tools=TOOL_SCHEMAS)`；把 assistant 消息（content + tool_calls）追加进 messages，`step += 1`；写 `model_response` trace。
   - **可见决策说明**：prompt 要求模型每次发起 `tool_calls` 时，在 content 里用一句不超过 40 字的话说明"这一步做什么"；agent 节点提取为 `decision_note`（`missing` 时记空串）写入 trace（模型可见输出，不是思维链）。**同时** agent 节点按写死规则生成 `decision_summary`：首步固定"首步：检索主题相关素材"（或 `首步：<实际工具>`），之后只拼接"上一步实际观察摘要 + 本步实际工具名/关键参数"，来源标 `orchestrator`；不推断模型动机，也不依赖模型是否写 content。
   - 思考模式的 `reasoning_content` 直接丢弃，不落盘、不进 state。
   - 模型一次返回多个 `tool_calls` 时全部保留，`id` 原样使用（缺失则生成 `call-<step>-<n>`）。
   - `terminal` 判定：含 `finish` ⇒ `finish`；含 `ask_user` ⇒ `ask_user`；两者同批 ⇒ `invalid`；无 tool_calls ⇒ `no_action`。
2. **tools 节点**：按模型返回顺序执行**全部** `tool_calls`，每个调用写一条 `tool_result` trace，并逐条回填 `{"role": "tool", "tool_call_id": <id>, "content": <JSON 字符串>}`；任何一条失败也照常回填错误观察，不中断整批。
   - `submit_script`：校验 schema，校验通过则更新 `draft`/`draft_revision`；失败则回填 `invalid_arguments`，不动草稿。
   - `estimate_duration` / `check_rules`：走注册表（参数校验 + 失败注入），成功则更新 `last_estimate`/`last_rule_report` 与对应 digest。
   - `ask_user`：存 `pending_question`；`finish`：存 `finish_outcome`；二者均为状态动作，回填 `{"ok": true}`。
   - 未知工具、参数非法都返回结构化错误观察，由模型自行纠正。
3. **循环**：回到路由；预算在每批工具执行后检查（见 §5.3）。

### 5.3 预算与上限（编排层强制）

| 项 | 默认 | 强制方式 | 触顶结果 |
| --- | --- | --- | --- |
| 模型步数 | 8（CLI 可调 1–20） | `agent` 节点前路由判断；第 8 步的工具批执行完后不再回 agent | **末步含 `finish` 或 `no_action` 且 finalize 复算通过 → `completed`**；末步主动结束但复算不过 → `failed`；**编排层强制收尾（触顶且未主动结束）→ `incomplete`**，`failure.code=step_limit` |
| 工具调用数 | `2 × 步数上限`（默认 16） | **批执行前预检**：`used + len(批) > max` ⇒ 本批不执行，trace 记 `skipped_tool_calls` 后收尾；批后 `used >= max` 即回 finalize（不进入下一轮 agent） | 已 `finish` 则按复算判定，否则 `incomplete`，`failure.code=tool_call_limit` |
| 单批 tool_calls | 不额外限制（受模型行为约束） | —— | —— |
| LangGraph 递归 | `2 × 步数上限 + 5` | `compiled.invoke(..., config={"recursion_limit": ...})` 作最后保险 | 捕获 `GraphRecursionError` → `failed`，`failure.code=orchestration_error` |

**边界语义（写死并测试）**：预算耗尽不等于未完成——只要模型在**最后一个允许步**主动结束（`finish` 或无 tool_calls 的 `no_action`）且 finalize 复算通过，就判 `completed`；只有"编排层强制收尾（触顶且未主动结束）/ 主动结束但复算不过"才得到 `incomplete` / `failed`。预算触顶时仍用最新提交稿做一次估算/规则复算，把未解决项写进 `failure.evidence`，不伪造通过。

### 5.4 典型步数

| 场景 | 动作序列 | 步数 |
| --- | --- | --- |
| 正常 | search×2（首查未命中→改写命中）→ submit → estimate+check → submit′ → estimate+check → finish | 7 |
| 无结果 | search×5（topic → 2-gram×3 → 回退词）→ ask_user | 6 |
| 可恢复工具失败 | search(失败) → search(重试) → search(改写) → submit → estimate+check → finish | 6 |
| 超时长修正 | search×2 → submit(38s) → estimate+check(超) → submit′(28s) → estimate+check(通过) → finish | 7 |
| 无法满足（时长冲突） | search×2 → submit(38s) → estimate+check(超时长+平台违规) → finish(cannot_satisfy) | 5 |
| 受控对照（只改目标） | 两组同为 38s 初稿；30s 组压缩到 28s、60s 组扩张到 58s | 各 7 |

> 上述为离线固定策略的确定性步数（详见 §11），真实模型步数可能更少或更多，硬性上限 8 步不变。

## 6. 工具设计

### 6.1 统一约定

- 工具规格用 JSON Schema 声明（原生 tool calling），参数由注册表**显式校验**（不依赖模型"应该传对"），校验失败返回 `invalid_arguments` 错误观察，不抛未捕获异常。
- 三个业务工具返回结构均为 `{"ok": true|false, ...}`；业务性的"空结果/不通过"仍是 `ok: true`（如检索 0 条、时长超限、规则有违规），只有工具执行失败才是 `ok: false`。模型可据此区分"结果不理想"与"工具坏了"。
- 工具上下文（目标时长、平台、数据集）由运行时绑定，不暴露给模型。
- 每个业务工具内部只做计算，不写任何文件；trace 由编排层统一写。

### 6.2 `search_materials`

```json
{
  "name": "search_materials",
  "description": "按关键词检索合成产品素材库，返回素材 ID、标题与事实原文。无结果时返回空列表。",
  "parameters": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "检索词，建议用短关键词", "maxLength": 120 },
      "top_k": { "type": "integer", "description": "返回条数，1–10，默认 5", "minimum": 1, "maximum": 10 }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

**算法（确定性）**：`query` 按空白与中英文标点切分为 token（去重、保序），每个 token 与素材 `title`（权重 3）、`tags`（2）、`facts[].text`（1）做大小写不敏感的子串匹配；每个 token 在每字段最多计一次；总分 >0 才命中；按 `(-score, material_id)` 排序，取前 `top_k`。**不做同义词/向量/模糊语义检索**——查不到时由模型改写查询（这是动态决策的观测点）。

返回示例：

```json
{
  "ok": true, "query": "字幕", "total_matched": 1,
  "materials": [{
    "material_id": "P01", "title": "字幕功能说明", "tags": ["字幕", "剪辑"],
    "facts": [{ "fact_id": "P01-F1", "text": "支持字幕", "kind": "feature" }],
    "score": 6, "matched_terms": ["字幕"], "matched_in": ["title", "tags", "fact"]
  }]
}
```

无结果：`{"ok": true, "query": "...", "total_matched": 0, "materials": [], "hint": "未命中素材；可改写成更短的关键词重试，或向用户请求补充信息。"}`

### 6.3 `estimate_duration`

```json
{
  "name": "estimate_duration",
  "description": "对分镜脚本做确定性时长估算，返回每镜时长、总时长、目标允许区间与是否达标。",
  "parameters": {
    "type": "object",
    "properties": {
      "shots": {
        "type": "array", "minItems": 1, "maxItems": 12,
        "items": {
          "type": "object",
          "properties": {
            "narration": { "type": "string", "maxLength": 120 },
            "caption": { "type": "string", "maxLength": 60 }
          },
          "required": ["narration", "caption"],
          "additionalProperties": false
        }
      }
    },
    "required": ["shots"],
    "additionalProperties": false
  }
}
```

返回示例：

```json
{
  "ok": true,
  "per_shot": [
    { "index": 1, "narration_chars": 25, "caption_chars": 8, "narration_s": 7, "caption_s": 2, "duration_s": 7 }
  ],
  "total_duration_s": 38,
  "target_duration_s": 30,
  "window": { "min_s": 27, "max_s": 33 },
  "within": false,
  "verdict": "too_long",
  "over_by_s": 5,
  "under_by_s": 0,
  "formula_version": "duration-v1"
}
```

`ok:false` 只在工具故障（注入）时出现；参数非法由注册表拦截为 `invalid_arguments`。

### 6.4 `check_rules`

```json
{
  "name": "check_rules",
  "description": "检查脚本的品牌禁用词、平台规范、夸大表述与事实引用，返回可定位的违规项。",
  "parameters": {
    "type": "object",
    "properties": {
      "title": { "type": "string" },
      "shots": {
        "type": "array", "minItems": 1, "maxItems": 12,
        "items": {
          "type": "object",
          "properties": {
            "visual": { "type": "string" },
            "narration": { "type": "string" },
            "caption": { "type": "string" },
            "citations": {
              "type": "array", "maxItems": 4,
              "items": {
                "type": "object",
                "properties": {
                  "material_id": { "type": "string" },
                  "fact_id": { "type": "string" },
                  "quote": { "type": "string" }
                },
                "required": ["material_id", "fact_id", "quote"],
                "additionalProperties": false
              }
            }
          },
          "required": ["visual", "narration", "caption", "citations"],
          "additionalProperties": false
        }
      }
    },
    "required": ["title", "shots"],
    "additionalProperties": false
  }
}
```

**检查项（合成规则集）**：

| 类别 | 检查 | 位置 | 严重级 |
| --- | --- | --- | --- |
| brand | 品牌禁用词（`banned_terms`，如"最强""第一""绝对""永久""100%""全网最低"） | title / narration / caption | error |
| platform | 平台禁用词（如站外导流"微信""二维码"等，按平台配置） | title / narration / caption | error |
| platform | 平台总时长上限（`max_total_duration_s`，规则 ID 取数据文件 `max_duration_rule_id`，如 `PLAT-kuaishou-DUR-001`） | 整稿 | error |
| exaggeration | 夸大表述正则（保证效果、绝对化承诺等） | title / narration / caption | error |
| citation | 引用存在性：素材 ID、事实 ID 必须存在 | 各镜 citations | error |
| citation | 原文一致性：`quote` 必须是事实原文的连续子串（去首尾空白后逐字比较） | 各镜 citations | error |
| citation | 事实支撑：旁白/字幕中命中 `claim_patterns` 的文本，必须出现在该镜已引用事实原文中；标题中的命中必须出现在全稿任一引用事实原文中 | narration / caption / title | error |
| structure | 同一镜旁白与字幕都为空 | 各镜 | warning |
| structure | 全稿无任何引用（提示确认未涉及产品事实） | 整稿 | warning |

- 扫描范围：`visual` 只作拍摄描述，不参与禁用词/事实支撑扫描（口径写入文档，避免误报）。
- 去重键 `(rule_id, shot_index, field, matched_text)`；输出按 `(shot_index is None, shot_index, rule_id, matched_text)` 稳定排序；超过 50 条截断并置 `stats.truncated=true`。
- `ok = errors == 0`；warning 不阻碍 `completed`，但必须进入输出 `risks`。
- 语义级误用（有引用但张冠李戴）自动检查不承诺识别，见 §17 限制。

返回示例：

```json
{
  "ok": false,
  "platform": "douyin",
  "violations": [
    {
      "rule_id": "CITE-001", "category": "citation", "severity": "error",
      "shot_index": 2, "field": "narration",
      "matched_text": "80%",
      "message": "效果数据「80%」没有素材事实支撑",
      "suggestion": "删除该数据，或从素材库找到原文并添加引用"
    }
  ],
  "stats": { "shots_checked": 5, "rules_checked": 41, "errors": 1, "warnings": 0, "truncated": false }
}
```

### 6.5 控制动作

| 动作 | 参数 | 语义 |
| --- | --- | --- |
| `submit_script` | `{title, shots[]}`（§4.3） | 提交/更新脚本草稿；同一次运行可多次提交，仅最新稿参与最终判定；返回 `{ok, revision, shots}` |
| `ask_user` | `{question(1–300), missing_fields[0–10], reason(可选)}` | 请求补充信息；整批执行完后结束为 `needs_input` |
| `finish` | `{outcome: "completed_intent"\|"cannot_satisfy", message(可选, ≤200)}` | 模型声明收尾意图；**最终状态由 finalize 复算决定**，模型声明不直接生效 |

约束：`finish` 与 `ask_user` 同批出现视为无效动作组合 → `failed`（`invalid_action_combination`）；控制动作的 schema 由注册表同样校验，失败回填 `invalid_arguments` 并继续循环。**控制动作校验失败时只回填错误并清除本批 `terminal`**，回到 agent 继续纠正；只有控制动作执行成功（或 finish+ask_user 同批的 invalid 组合）才进入 finalize，避免"参数打错就直接失败"。

## 7. 时长估算公式（确定性，写死）

**常量（`tools/duration.py`，版本 `duration-v1`）**：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `NARRATION_DIVISOR` | 4 | 旁白语速：4 字/秒（240 字/分钟） |
| `CAPTION_DIVISOR` | 5 | 字幕阅读速度：最多 5 字/秒 |
| `MIN_SHOT_S` | 2 | 单镜最短时长 |
| `TOLERANCE_LOW` / `TOLERANCE_HIGH` | 0.9 / 1.1 | 目标时长 ±10% 允许区间 |
| `MAX_SHOTS` | 12 | 分镜数上限 |
| `MIN_TARGET_S` / `MAX_TARGET_S` | 5 / 300 | 目标时长合法范围 |

**公式**（全程整数运算，不用浮点）：

```
chars(s)        = len(re.sub(r"\s+", "", s))            # 删除空白字符，标点计入
narration_s     = ceil(chars(narration) / 4) = (n + 3) // 4
caption_s       = ceil(chars(caption)   / 5) = (c + 4) // 5
shot_s          = max(2, narration_s, caption_s)
total_s         = Σ shot_s
window_min      = ceil(0.9 × target) = (9 × target + 9) // 10
window_max      = floor(1.1 × target) = (11 × target) // 10
within          = window_min ≤ total_s ≤ window_max
```

**判定**：`total_s > window_max` ⇒ `too_long`，`over_by_s = total_s - window_max`；`total_s < window_min` ⇒ `too_short`，`under_by_s = window_min - total_s`；否则 `within`。**模型只能通过改旁白/字幕文本或分镜数量影响时长，无法自填时长。**

**演算示例（公式说明，非运行证据）**：目标 30 秒 → 区间 `[27, 33]`；6 镜旁白字数 `25/26/27/22/23/18`（字幕均很短）→ 逐镜 `7/7/7/6/6/5` → 总 38 秒，`too_long`（超 5 秒）；压缩为 5 镜旁白字数 `22/23/24/18/19` → `6/6/6/5/5` → 总 28 秒，`within`。该 38→28 演算对应 AC-05，离线固定策略会确定性地复现这两个数。

**参考实例与真实模式口径（核心证据）**：AC-05 离线正式用例要求"首稿估算 38 秒（>30）→ 压缩 → 重新估算 28 秒（≤30 且 within）"，确定性复跑，即 `27 ≤ total_s ≤ 30`。**真实模式运行时只用通用 ±10% 区间**：最终 27–33 且引用可回溯即 `completed`（≤30 记"与参考实例一致"，31–33 记"在容差内、高于参考实例"），不要求 38→28，不因未压到 ≤30 判 `failed`；真实首稿/最终值如实记录，压缩分支未触发则标注。正式真实模式决策证据改用刺激必然发生的三条场景（AC-03a、AC-08b no_hit、AC-04a，见 01 §7.2 / 本文 §15.4.5）。

**受控对照的公式含义**：同一份 38 秒初稿，在目标 30 秒下判为 `too_long`（须压缩），在目标 60 秒下区间 `[54, 66]` 判为 `too_short`（须扩张）——这是 AC-08a"只改目标时长、下一步动作方向随之改变"的确定性依据。

**一致性强校验**（finalize 与测试双重执行）：`output.script.shots[i].duration_s` 必须等于公式对该镜文本的复算值；`estimated_total_duration_s` 必须等于逐镜之和与估算结果三处一致；任何不一致直接按内部错误处理（不得输出 `completed`）。

## 8. 规则检查设计

### 8.1 规则来源与加载

- 品牌规则、平台规则、事实支撑模式在 `data/` 静态 JSON 中维护，`dataset.py` 启动时校验并冻结（不运行期修改）。
- 平台由输入决定，未知平台输入校验阶段即 `needs_input`。
- 规则集为合成近似（内部试用规范），不是真实平台条款或法务意见；文档与 README 显著标注，避免被当成合规依据。

### 8.2 检查流程

1. 结构检查：分镜非空、字段类型/长度（注册表已挡一层，finalize 复算时再校验）；空镜 warning。
2. 文本扫描：对 title/各镜 narration/caption 依次跑品牌禁用词、平台禁用词、夸大正则；命中生成 `RuleViolation`。
3. 平台时长：复用 `tools/duration.py` 的纯函数算整稿总时长，与平台 `max_total_duration_s` 比较；命中时使用数据文件中的 `max_duration_rule_id`（如 `PLAT-kuaishou-DUR-001`），不靠代码拼规则 ID。
4. 引用校验：对每镜 citations 做"素材存在 → 事实存在 → quote 逐字子串"三段检查。
5. 事实支撑：从 narration/caption 提取 `claim_patterns` 命中文本，检查是否出现在该镜引用事实原文集合中；标题中的命中检查全稿引用集合。
6. 聚合去重、排序、截断，计算 `ok/stats`。

### 8.3 误报与漏报口径（诚实声明）

- **漏报**：语义级事实误用（引用真实但结论张冠李戴）、未命中 claim 模式的软性夸大（如"效果惊艳"）、非中文表述，自动检查无法保证识别。
- **误报**：标题/口播中的普通数字（若命中参数模式）可能被要求补引用；由模型改写规避，验收不要求真实模型零误报（要求最终 error 为 0）。
- 兜底手段：系统提示词强约束 + 引用逐字校验 + 内部试用人工评审 + 验收场景固定断言。评审标准以"error 违规为 0、引用可逐字回溯"为硬线，不承诺语义等价。

### 8.4 最终校验（finalize）

收尾节点不信任模型提交的工具结果，对**最新 `submit_script` 草稿**独立复算：

1. 无草稿 → `failed`（`no_script_submitted`；若工具错误历史存在，`failure` 引用工具错误）。
2. 调用纯函数 `estimate()` 与 `check()`，写入 `final_checks`；同时比较 `estimate_digest/rules_digest` 与草稿 digest，不一致则记 warning（模型可能检查了别的脚本）。
3. 状态判定（按顺序）：
   - `pending_question` 存在 → `needs_input`；
   - `terminal == invalid` → `failed`（`invalid_action_combination`）；
   - `terminal == finish` 且 `estimate.within && rules.ok` → `completed`（即使模型声明 `cannot_satisfy`，以复算为准，并附风险说明）；
   - `terminal == finish` 且校验不过 → `failed`（`finish_outcome == cannot_satisfy` 时为 `cannot_satisfy`，否则 `checks_failed_after_finish`，evidence 带最终估算/违规明细）；
   - `terminal == no_action`：校验通过 → `completed`（无 tool_calls 也是 AGENTS 允许的终止条件）；不通过 → `failed`（`model_terminated_without_finish`）；
   - 预算触顶 → `incomplete`（`step_limit` / `tool_call_limit`），附最终草稿与未解决项。**注意**：末步 `no_action` 且复算通过已在上一分支判 `completed`，不因预算触顶改判；只有编排层强制收尾才 `incomplete`。
4. 组装风险：warning 级违规、工具错误历史摘要、digest 不一致提示、"模型声明无法满足但复算通过"提示。

## 9. 状态机

```
                     ┌────────────────────────────┐
 输入校验 nees_input │  graph:                   │
 └───────────────→  │  START → agent ⇄ tools     │
                     │            ↓              │
                     │          finalize → END   │
                     └────────────────────────────┘
                                    ↓
        completed / needs_input / incomplete / failed
```

| 当前 | 事件 | 下一状态/动作 |
| --- | --- | --- |
| `START` | 输入缺失/非法/平台未知 | `needs_input`（不调模型） |
| `START` | 输入合法 | `agent`（step=0） |
| `agent` | 返回多个 tool_calls | 全部执行后回 `agent`（预算内） |
| `agent` | 含 `finish` | 执行整批 → `finalize` |
| `agent` | 含 `ask_user` | 执行整批 → `finalize(needs_input)` |
| `agent` | 无 tool_calls | `finalize`（按无 action 分支判定） |
| `tools` | 达步数/工具调用上限 | `finalize(incomplete)` |
| `tools` | 正常 | `agent` |
| `finalize` | 复算通过 | `completed` |
| `finalize` | 请求补充 | `needs_input` |
| `finalize` | 复算不过/无法满足/模型提前终止 | `failed` |
| `finalize` | 预算触顶 | `incomplete` |

状态不可回退（`completed` 不允许在后续步骤被推翻）；单次运行一个状态。退出码映射见 `01` §4.3。

## 10. trace 字段规范

### 10.1 文件与公共字段

- 路径：默认 `runs/<run_id>/trace.jsonl`；验收时复制到 `examples/<场景>/trace.jsonl` 并提交。
- JSONL，一行一事件，UTF-8，`ensure_ascii=False`，写后 flush，运行失败也保留已写事件。
- 公共字段：`seq`（自增，从 1 开始）、`ts`（ISO-8601 带时区）、`run_id`、`type`、`step`（当前步，校验/收尾事件可复用最近步）。

### 10.2 事件类型与字段

| type | 何时写 | 主要字段 |
| --- | --- | --- |
| `run_start` | 图启动前 | `mode`、`input`（无密钥）、`budget`、`data_digest`、`formula_version`、`model`（真实模式）、`injection`（注入配置摘要） |
| `input_validation` | 输入校验后 | `ok`、`missing_fields[]`、`invalid_fields[]`、`status`（needs_input 时） |
| `model_response` | 每次模型调用后 | `step`、`response_id`（真实模式为 DeepSeek 响应 `id`；离线 mock 为合成 ID；用于人工交叉核对）、`decision_note`（模型在 content 中的一句 ≤40 字本步说明，可能为 `""`）、`decision_note_source`（`model`/`missing`）、`decision_summary`（编排层生成的事实摘要：上一步实际观察摘要 + 本步实际工具名/关键参数；首步固定为"首步：检索主题相关素材"或"首步：<实际工具>"，不推断模型动机）、`decision_summary_source`（恒为 `orchestrator`）、`content_excerpt`（≤200 字可见回复，供审计，非思维）、`tool_calls[{name, arguments}]`、`finish_reason`、`usage{prompt_tokens, completion_tokens}` |
| `tool_result` | 每个 tool_call 执行后 | `step`、`tool`、`call_id`、`ok`、`duration_ms`、`injected`、`error{code,message,retryable}`、`observation`（结构化摘要，如 `{"total_matched":1,"material_ids":["P01"]}`、`{"total_duration_s":38,"within":false,"window":[27,33]}`、`{"errors":2,"rule_ids":["CITE-001"],"shot_indexes":[2]}`）、`decision_hint`（机械摘要，如 `no_hits:rewrite_or_ask_user`，由观察结果规则化生成，不是模型思维） |
| `finalize` | 收尾节点 | `step`、`status`、`failure_code`、`estimate`（摘要）、`rules`（统计+违规 ID）、`mismatch`、`budget` |
| `run_end` | 输出组装后 | `status`、`steps_used`、`tool_calls_used`、`duration_ms`、`exit_code` |

**`decision_summary` 来源声明**：它是编排层按写死规则拼装的事实摘要（上一步实际观察摘要 + 本步实际工具名/关键参数），**不是模型输出、不推断模型动机**；`decision_note` 才是模型可见 content。`docs/03`、`docs/04` 与 `examples` README 必须如实区分两列来源；模型 content 为空时 `decision_note_source=missing`，证据链由 `decision_summary` 补足。

### 10.3 禁止与脱敏

- 禁止写入：`reasoning_content`（连同 `reasoning`、`thinking` 等键名在写盘前统一丢弃）；`LLM_API_KEY` 值；`.env.local` 全文；原始 HTTP 请求头。
- 脱敏：`TraceWriter` 接收密钥值，递归替换任何字符串中出现的密钥为 `***`；测试断言 trace 中不出现密钥值、不出现 `reasoning_content`。
- trace 允许包含合成素材原文与模型生成的脚本/纯文本回复（均为内部试用数据）；不含用户真实隐私（输入为创作者自填，试用阶段自行负责）。
- **来源约束**：`examples/` 中的 trace 只能是实际运行产物，禁止手写/补写/编辑；具体规则见 §15.4.4。
- trace 大小：单次运行典型 10–20 个事件，脚本参数每个约 1–2 KB；不做压缩，方便评审阅读。

## 11. 真实 / 离线两种模式

| 维度 | 真实模式 | 离线模式（`--offline`） |
| --- | --- | --- |
| LLM 客户端 | `llm.deepseek.DeepSeekClient`（httpx） | `llm.mock.MockClient`（固定策略） |
| 配置 | 读 `.env.local`：`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`；缺任一 → 退出码 1，明确报错，不打印值 | 不需要密钥/网络 |
| 决策质量 | 真实模型决策与生成质量 | 只证明编排/工具/校验/状态链路，**不证明模型决策质量** |
| 确定性 | 文本非确定；硬约束判定确定 | 全流程确定（38→28 等固定数值可复现） |
| 用途 | 验收演示、DoD 真实 trace、文档证据 | 自动化测试、无网复跑、CI |
| 差异点 | 仅 LLM 客户端与 run_start 的 mode/model 字段 | 同左 |

真实模式参数：`temperature=0.0`、`max_tokens=4096`、`tool_choice="auto"`、`stream=false`；思考模式不主动关闭（`tool_choice` 只允许 auto，符合实测约束），响应中的 `reasoning_content` 一律丢弃；HTTP 超时 60 秒，超时/5xx 重试 2 次（间隔 1 秒、2 秒），仍失败则本次运行 `failed`（`llm_error`）**、退出码 5，仍有 output/trace 便于复盘**，不伪造结果。配置缺失、数据集不可用、未捕获异常才是内部错误（退出码 1）。

**base URL 口径**：`.env.example` 统一写"含 `/v1` 的 OpenAI 兼容根"（如 `https://api.deepseek.com/v1`）；客户端 `normalize_base_url()` 去掉尾部 `/`，若不以 `/v1` 结尾自动补 `/v1`，再拼 `/chat/completions`，因此 `.env.local` 写成不带 `/v1` 的根地址（当前实际值）也能工作。README 写明该口径。

**离线 mock 的边界（必须写进 README 与评估文档）**：mock 是"读取真实工具返回结果后按固定规则作决策"的策略实现，用于验证流程分支与受控对照；它不会生成真实模型质量的文案，不能作为模型能力证据。mock 可读取合成数据集来构造引用与查询词（仅为离线链路演示，真实模型只能通过工具看到数据）。固定策略：

1. **检索（确定性序列，与数据无关地写死）**：
   - 第 1 次查询 = `topic` 原文；
   - 第 2–4 次 = 去掉固定停用词（`怎么/如何/攻略/教程/技巧/新手/入门/方法/我的`）后，按字符顺序取前 3 个不重复 2-gram（不足 2 字或与已发查询相同则跳过）；
   - 第 5 次 = 固定回退词 `星河旅拍指南`（测试锁死其在默认数据与空素材夹具上均 0 命中）；
   - 任一次命中即进入写稿；全部 0 结果 → `ask_user`。保证 AC-03a / AC-08b no_hit 稳定得到 ≥2 个不同查询且全部 0 结果。期望查询词序列写入 `examples` 对应 README（AC-03a：`火星旅拍攻略 → 火星 → 星旅 → 旅拍 → 星河旅拍指南`；no_hit 同主题空数据集：`字幕功能上线 → 字幕 → 幕功 → 功能 → 星河旅拍指南`）。
   - 工具错误 → 重试一次；持续失败 → `finish(cannot_satisfy)`。
2. **初稿固定 38 秒 / 6 镜（与目标时长无关）**：命中素材时按事实原文写引用（如 P01-F1"支持字幕"）。这让受控对照共享同一初稿。
3. **按估算结果改稿**：`too_long` → 压缩到 28 秒（5 镜）；`too_short` → 扩张到 58 秒（9 镜）；`within` → 进入规则处理。同一初稿在 30 秒目标下压缩、在 60 秒目标下扩张，动作方向随目标改变（AC-08a 确定性证据）。
4. **规则处理**：error 可改则改后重查；无法改（如目标区间下限大于平台时长上限）→ `finish(cannot_satisfy)`。
5. **通过**：最终估算 `within` 且规则 0 error → `finish(completed_intent)`。

离线证据必须标注 `mode=offline` 且说明"只证明流程，不证明模型决策质量"；AC-05 核心证据（38→28）与 AC-08 对照均以离线固定策略为确定性基准，真实模型证据另存 `examples/.../real/` 子目录并如实标注实际数值。

## 12. 工具失败注入机制

### 12.1 配置方式

输入 JSON：

```json
{
  "failure_injection": [
    { "tool": "search_materials", "error": "timeout", "call": 1 },
    { "tool": "estimate_duration", "error": "unavailable" }
  ]
}
```

CLI（可重复，与 JSON 合并，同一工具只允许一条规则，重复即用法错误退出 2）：

```text
--inject-failure search_materials=timeout@1
--inject-failure estimate_duration=unavailable
```

`call` 为 1 起的调用序号，省略表示该工具**每次**调用都失败。仅允许三个业务工具；注入控制动作或不存在的工具 → 用法错误（退出 2），避免演示配置错误被静默忽略。

### 12.2 执行语义

- `ToolRegistry` 为每次运行维护 `{tool: 调用次数}`；每次业务工具调用先计数，再判断注入是否命中；命中则**不执行**工具本体，抛出内部 `ToolExecutionError(code, retryable)`。
- 错误码：`timeout`（retryable=true）、`unavailable`（true）、`invalid_response`（false，模拟工具返回坏数据）。工具节点捕获后回填 `{"ok": false, "error": {...}}` 观察，写 trace（`injected=true`），并把记录追加进 `tool_errors`。
- 失败注入只影响**模型可见的业务工具调用**；finalize 的内部复算走纯函数，不受注入影响，保证最终判定真实。工具失败历史必须出现在输出 `tool_errors` 与 `risks`，满足"不掩盖"。
- 确定性：同一输入 + 同一注入配置 ⇒ 同一调用序号失败；不依赖时间/随机数。

## 13. LangGraph 集成与取舍

### 13.1 LangGraph 承担什么

- `StateGraph` 显式建图：节点、条件边、`START/END`；
- 状态通道与 reducer（`messages`/`tool_errors` 用 `operator.add` 累积，其余覆盖）；
- 循环执行与 `compile()/invoke()`；
- `recursion_limit` 作为预算外的兜底保护；
- 节点级输入/输出边界清晰，单测可直接构造状态调用节点函数。

### 13.2 我们自写、不使用预构建件

| 不用 | 原因 |
| --- | --- |
| `create_react_agent` / `langgraph.prebuilt.ToolNode` | 需要精确控制：一次响应多个 `tool_calls` 的顺序执行与逐条回填、失败注入、`submit_script/ask_user/finish` 状态动作、trace 字段、预算触顶行为；预构建件把这些藏在内部，不可判定 |
| `ChatDeepSeek` 等 LangChain 模型封装 | 需求方给了明确的 HTTP 契约（`tool_calls` JSON 字符串、`reasoning_content`、思考模式 tool_choice 限制）；直接用 httpx 少一层序列化，响应字段可控、可测；离线 mock 也更容易同接口实现 |
| Checkpointer / 持久化 | 单次运行、无多轮会话，不引入数据库/持久化 |
| LangSmith 等托管观测 | 本地 trace 已足够，且不能把内容外发到第三方 |

### 13.3 取舍与代价

- 收益：编排显式、终止与预算可判定、每一步可观测；模型/工具/校验解耦，可分别测试；真实/离线切换只换 LLM 客户端。
- 代价：图很简单（3 节点），LangGraph 的价值主要在状态通道与循环骨架，算力/复杂度收益有限；版本升级需跟 API（已 pin 1.2.18）。若后续需求简化为单次调用，可评估去掉 LangGraph——当前为满足工程契约（`AGENTS.md`）"显式 StateGraph + 逐步骤可观测"的要求而保留。
- 我们"改了什么"：没有替换 LangGraph 的图执行；在节点内部自写 agent/tool/finalize 语义、自写 DeepSeek 客户端、自写 trace、自写预算与失败注入，图只承担骨架。

### 13.4 修改结果（可核对）

上面这些改动的收益不是口头结论，都能在仓库里对上：

| LangGraph 的职责 | 我们额外做的封装/修改 | 可核对的结果 |
| --- | --- | --- |
| 图执行与循环 | 三节点显式图；agent/tools/finalize 语义自写 | `examples/04_tool_failure/a_recoverable/real/trace.real.jsonl`：注入失败后模型仍按观察继续调工具并完成 |
| 状态通道 | `messages`/`tool_errors` 用 reducer 累积；草稿、digest 等业务状态自管 | `tests/test_runner.py` 覆盖一次响应多个 `tool_calls` 的逐条回填与状态更新 |
| 终止与预算 | 自写路由与批前预算预检；`recursion_limit` 只当兜底 | `examples/07_step_limit/output.offline.json`（`incomplete`/`step_limit`、保留草稿）；跳过批含 `finish` 的边界用例 |
| 模型接入 | httpx 直连 DeepSeek，不用 `ChatDeepSeek` | 真实 trace 的 `model_response` 带 DeepSeek `response_id`/usage；无 `reasoning_content`；`tests/test_deepseek.py` |
| 观测 | 自写 JSONL trace，字段按验收需要设计 | `docs/03-evaluation.md` §3.1 两条真实决策证据可在 trace 中逐事件核对 |
| 最终判定 | finalize 独立复算，模型 `finish` 只作意图 | `examples/06_unsatisfiable/output.offline.json`：明确 `failed` 并给冲突证据，不因模型声明而放过 |

这也是选型的实际代价/收益：如果用 `create_react_agent` 之类的预构建件，多 `tool_calls` 的回填、失败注入、预算边界和 trace 字段都不受我们控制，上面这些证据就产不出来；所以这里保留 LangGraph 的骨架、把可判定性放在自己手里，而不是为了"用框架"而用。

## 14. 技术选型与依赖

| 选择 | 版本/方案 | 理由 | 放弃的方案 |
| --- | --- | --- | --- |
| Python | 3.13.5（`.venv`） | 需求方指定，已验证依赖可装 | 其他版本 |
| 编排 | langgraph `==1.2.18` | 工程契约（`AGENTS.md`）要求显式 StateGraph；已验证 | 手写循环（不满足工程契约）、`create_react_agent` |
| 消息类型底座 | langchain-core `==1.7.0` | 与 langgraph 验证配对；只作为依赖锁定，不直接使用其消息类 | 不锁（版本漂移风险） |
| HTTP/模型 | httpx `==0.28.1` | 直接说 DeepSeek HTTP 契约，零黑盒 | openai SDK / ChatDeepSeek（多一层封装） |
| 配置 | python-dotenv `==1.2.4` | 解析 `.env.local`，避免手写边界情况 | 手写 parser |
| 测试 | pytest `==9.2.0` | 单命令跑完；无插件依赖 | unittest |
| 数据格式 | JSON（标准库） | 数据量小、无额外依赖、diff 友好、评审可直接看 | YAML（需额外直接依赖）、SQLite（超出范围） |
| 领域模型 | dataclass + TypedDict | 轻量、显式校验、无魔法；错误信息可控 | pydantic 模型（多一层抽象，收益有限） |
| 运行产物 | 文件（`runs/`，gitignored） | 无数据库要求；证据可复制到 `examples/` | 数据库、服务化 |
| 观测 | 自写 JSONL trace | 契约字段完全可控、可测、可脱敏 | 日志框架、LangSmith |
| 日志 | 仅 stderr 错误 + trace | 避免日志与 trace 两套事实源 | logging 配置体系 |

依赖方向：`requirements.txt` 精确锁直接依赖；传递依赖由 pip 解析，实现时已记录实际解析结果（langgraph 会带入 langgraph-sdk/checkpoint/prebuilt、pydantic 等，属传递依赖，不直接 import）。禁止在代码中 import 未在 `requirements.txt` 声明的库（标准库除外）。

## 15. 测试计划

### 15.1 命令

```powershell
$env:PYTHONUTF8=1
.venv\Scripts\python -m pytest -q          # 默认：离线、无网络，一条命令跑完（目标 ≤60s）
.venv\Scripts\python -m pytest -q -m real  # 真实模型冒烟（需 .env.local，默认跳过）
```

项目根 `pytest.ini`：注册 marker `real`（真实模型）与 `evidence`（证据一致性）；默认 `addopts = -m "not real"`，即离线测试与证据一致性测试默认执行；真实冒烟用 `-m real` 覆盖启用。

### 15.2 分层与用例矩阵

| 层 | 文件 | 用例（断言行为，不断言日志） |
| --- | --- | --- |
| 单元 | `tests/test_duration.py` | 公式逐镜/总时长；30 秒窗口 27–33；38→28 演算；空字幕/纯字幕/超长文本；下限 5 秒与上限 300 秒；整数运算无浮点误差；`duration_s` 与文本复算一致 |
| 单元 | `tests/test_materials.py` | "字幕"命中 P01；多 token 权重与排序稳定；top_k 截断；无结果返回空且 `ok=true`；空查询/超长查询参数错误 |
| 单元 | `tests/test_rules.py` | 品牌禁用词命中 title/narration/caption；平台禁用词按平台生效；平台时长上限（规则 ID 来自 `max_duration_rule_id`）；夸大正则；引用素材/事实不存在；quote 非原文子串；"80%"无引用命中 CITE-001；有原文引用的数字通过；空镜 warning 不阻塞；`ok` 只由 error 决定；去重/排序/截断 |
| 单元 | `tests/test_mock.py` | 检索查询序列确定性：原文 → 去停用词 2-gram×3 → 固定回退词；全部 0 结果 → `ask_user`（含空素材夹具）；回退词在默认数据与空素材夹具上 0 命中；30s → 38→28、60s → 38→58；平台时长冲突 → `cannot_satisfy`；检索工具错误重试一次、持续失败 `cannot_satisfy`；每一步 `content` 非空且 ≤40 字 |
| 单元 | `tests/test_deepseek.py` | base_url normalize（带/不带 /v1）；URL 与鉴权头；多个 tool_calls 解析；`reasoning_content` 不进入返回结构；arguments 非法 JSON → `arguments_error`；finish_reason=length → 截断错误；5xx/网络错误重试；4xx 不重试且错误消息不含密钥 |
| 集成 | `tests/test_finalize_guard.py` | **独立证明（Q2）**：草稿旁白含"效率提升 80%"且无引用 → 规则检查命中 `CITE-001` error；模型已 `finish` 时 finalize 仍判 `failed`，绝不 `completed`；同一文本配无关引用（P01 原文不含 80%）仍 error；构造含"效率提升 80%"原文的测试数据集并正确引用时可通过（证明不是一刀切禁词） |
| 单元 | `tests/test_dataset.py` | 三个 JSON 加载与 schema 校验；重复 ID 报错；正则编译失败报错；**空素材数组合法**（AC-08b 夹具）；默认数据约束由独立测试锁死（P01 恰好一条事实、素材无效果数据）；平台 `max_duration_rule_id` 必填且格式/唯一性校验；固定回退词 `星河旅拍指南` 在默认数据上 0 命中；digest 稳定与内容变更 |
| 单元 | `tests/test_registry.py` | 注入第 1 次失败、后续成功；无条件注入每次失败；参数非法返回 `invalid_arguments`；未知工具错误；调用计数准确；控制动作不可注入 |
| 单元 | `tests/test_state.py`、`tests/test_trace.py`、`tests/test_prompt.py`、`tests/test_config.py` | 预算换算与非法值；trace JSONL 公共字段/seq/ts、密钥脱敏、递归移除 `reasoning_content`、覆盖写；prompt 含硬约束与 `decision_note` 要求；config 缺变量报错不泄漏值、`LLM_PROXY` 透传 |
| 集成 | `tests/test_runner.py` | 正常 → `completed` 且 trace 顺序正确；**AC-05 核心数值**：首稿 38 秒（`>30`、`too_long`）→ 改稿 → 最终 28 秒（`≤30` 且 `within`）；**受控目标对照**：同数据下 target=30 → 38→28（压缩）、target=60 → 38→58（扩张），两条 trace 的 verdict/动作方向不同；**受控检索对照**：默认数据 → 命中→`completed` 且引用 P01，`--data-dir` 空素材变体 → ≥2 次 0 结果→`ask_user`→`needs_input`；可恢复失败 → `completed` 且 `tool_errors` 有注入记录；持续失败 → `failed`；**步数上限边界**：最后一个允许步 `finish` 且复算通过 → `completed`，同预算未 `finish` → `incomplete`；**工具调用预算 16/17 边界**：已用 16 时下一批预检失败 → 不执行、trace 有 `skipped_tool_calls`、`incomplete(tool_call_limit)`；15+2 同样整批跳过；`--max-steps 3` → `incomplete`；finish 与 ask_user 同批 → `failed`；**CITE-001 兜底**：旁白"效率提升 80%"无引用时即使 finish 也判 `failed`；`decision_note` 为空时 `decision_note_source=missing` 且 `decision_summary` 非空、有 content 时两者都在；输出时长/引用三方一致；每步 trace 无 `reasoning_content` |
| 集成 | `tests/test_output.py` | 输出字段齐全；引用三元组全部可回溯；逐镜时长与总时长三方一致；`completed` 蕴含 `within && rules.ok`；`failed` 必有 `failure`；trace 无 `reasoning_content`、无密钥值；`data_digest`/`budget`/`trace_path` 正确（当前由 `test_runner.py` 覆盖，文件合并实现） |
| 集成 | `tests/test_examples_evidence.py` | marker `evidence`；默认执行；遍历 `examples/`：用例目录必须含 input/command/output/trace/README，`output.<mode>.json` 与 `trace.<mode>.jsonl` 的 run_id/status/mode 一致，trace 为合法 JSONL 且首尾为 `run_start`/`run_end`；**全部离线 command 在临时目录复跑，逐条比对 status/退出码/总时长/步数/工具调用数/注入标记/needs_input**；`model_response.response_id` 必须存在；**缺失/不一致/空 examples → fail，不允许 skip/空跑**；真实性由需求方按 `docs/03` 复跑比对，测试不假装能验证真实调用本身 |
| 端到端 | `tests/test_cli.py` | 子进程跑 `python -m src.cli run` 各离线场景（AC-01–AC-08）；stdout 是合法 JSON；退出码 0/3/4/5 对应状态；缺失 `audience` → 3；输入 JSON 与 CLI 参数合并/覆盖；`--offline` 无密钥可跑；未知平台 → 3；非法注入 → 2；`--data-dir` 对照实验可复跑；AC-05/AC-08 命令可在 `examples/` 目录按 `command.*.txt` 原样复跑 |
| 真实 | `tests/test_real_smoke.py -m real` | 正式真实决策证据跑 AC-03a / AC-08b no_hit / AC-04a（刺激必然发生，分别断言连续 0 结果有可见反应且无编造、与 hit 组动作不同、注入失败后重试/调整）；AC-05 real、AC-08a real 作为加分证据另跑并如实记录；断言硬约束（区间、引用可回溯、规则 error 0）；无密钥自动 skip |

### 15.3 测试原则

- 断言**行为与数据**（状态、时长数值、引用映射、违规 ID、trace 事件结构），不断言日志文本；trace 断言用事件类型/字段，不做整行字符串比对。
- 离线测试完全不碰网络与 `.env.local`；真实测试 marker `real`，证据测试 marker `evidence` 默认执行（`pytest.ini` 默认 `-m "not real"`）；真实冒烟用 `-m real` 覆盖启用。
- 固定数据、固定 mock、固定注入序号 ⇒ 结果确定；涉及时间的字段（`ts`/`run_id`/`generated_at`）不参与相等断言，只校验格式。
- 每个模块提交前跑对应测试；全部模块完成后跑全量 + 真实冒烟，证据存 `examples/`。

### 15.4 examples/ 证据与 trace 来源约束

#### 15.4.1 目录结构

```
examples/
  01_normal/              input.json  command.offline.txt  command.real.txt  output.offline.json  trace.offline.jsonl  output.real.json  trace.real.jsonl  README.md
  02_missing_input/       同上（仅 offline；输入校验不调模型）
  03_no_result/           a_no_hit/（持续无结果→needs_input）、b_rewrite_hit/（改写命中→completed）
  04_tool_failure/        a_recoverable/（timeout@1→重试成功）、b_persistent/（持续 unavailable→failed）
  05_over_duration/       offline/（核心：38→28）、real/（真实模型实际 trace）
  06_unsatisfiable/       kuaishou 上限 25 vs 目标 30 → failed
  07_step_limit/          --max-steps 3 → incomplete
  08_controlled_target/   target_30/、target_60/、README.md（同主题，仅目标时长不同）
  09_controlled_retrieval/ hit/、no_hit/、no_hit_data/（三个 JSON 齐全：素材数组为空、品牌/平台规则同默认数据）、README.md（同主题，仅检索结果不同）
  pytest-offline.txt      # 全量离线测试输出（实际命令重定向，docs/03 引用）
  pytest-real.txt         # 真实模型冒烟测试输出（实际命令重定向，docs/03 引用）
```

每个用例目录至少包含：`input.json`（真实输入）、`command.<mode>.txt`（在仓库根目录可原样执行的完整命令，含 `PYTHONUTF8=1`）、`output.<mode>.json`、`trace.<mode>.jsonl`、`README.md`（mode、关键数值、判定结论、命中的分支）。README 必须写清 trace 两列来源：`decision_note`/`decision_note_source` 来自模型可见 content，`decision_summary`/`decision_summary_source=orchestrator` 是编排层事实摘要，不能混称模型输出。真实/离线并存时用文件名后缀区分，禁止互相替代。`examples/pytest-offline.txt` 与 `examples/pytest-real.txt` 由实际 pytest 命令的 stdout/stderr 重定向生成，`docs/03` 引用；同样禁止手写。

#### 15.4.2 核心证据（AC-05 参考实例）

- `examples/05_over_duration/offline/`：固定策略确定性运行，必须复现"首稿 38 秒（>30，too_long）→ 改稿 → 重新估算 28 秒（≤30 且 within）"；README 写清两次估算值、两次提交的差异、最终引用 P01"支持字幕"、复跑命令。
- `examples/05_over_duration/real/`：同一输入真实模型运行，保留实际 trace；README 记录实际首稿/最终估算、改稿次数；**该组只作加分证据，不作唯一依赖**。真实模式判定只用通用区间：最终 27–33 且引用可回溯即 `completed`（≤30 记"与参考实例一致"，31–33 记"在容差内、高于参考实例"）；未触发压缩分支或最终不在区间均如实记录；**不得为了凑 38→28 改写或补写 trace，也不得因未压到 ≤30 强行判 failed**。
- 正式真实模式决策证据见 §15.4.5（三条刺激必然发生的场景）；核心证据的结论表述必须区分"离线流程证据"与"真实模型决策证据"。

#### 15.4.3 受控对照证据（AC-08）

- `08_controlled_target`：`target_30` 与 `target_60` 的输入除 `target_duration_s` 外逐字段相同（同主题/受众/平台/风格/同一 dataset）。离线确定性要求：两组首稿同为 38 秒；30s 组 `too_long` → 28 秒，60s 组 `too_short` → 58 秒；README 对比表列出首稿估算、verdict、改稿方向、最终总时长、分镜数、动作序列实际值。真实模型两组均跑，只作加分证据：断言各自落在通用区间与引用可回溯，实际数值填表；**不硬性要求方向差异**，出现一次成稿/未改稿如实标注，不得补写。
- `09_controlled_retrieval`：`hit` 用默认 `data/`；`no_hit` 用 `--data-dir examples/09_controlled_retrieval/no_hit_data/`（`materials.json` 素材为空、品牌/平台规则与原数据相同），两组输入逐字段相同。离线确定性要求：`hit` → 检索命中 → 提交/估算/规则 → `completed` 且引用 P01；`no_hit` → ≥2 次检索均为 0 条 → `ask_user` → `needs_input`。README 对比表列出每次查询词、结果数、工具调用序列、最终状态。真实模型 `hit` 与 `no_hit` 两组均跑，其中 `no_hit/real` 是正式决策证据（见 §15.4.5），判定同 01 §7.2。
- 对照结论必须引用具体 trace 事件（如两次 `tool_result` 的查询词与 `total_matched`、两次 `model_response` 的 `tool_calls` 名称与参数），不得只写主观结论。

#### 15.4.4 trace 来源约束（硬性）

1. `examples/` 下所有 trace 必须由本仓库 CLI 实际运行生成（真实模型或 `--offline`）；**禁止手写、补写、拼接、人工编辑 trace**。若需要修正证据，只能重新运行生成并整文件覆盖。
2. 文件名后缀、`trace.run_start.mode`、`output.mode`、目录 README 声明四者必须一致；离线证据必须显式标注"只证明流程，不证明模型决策质量"。
3. 真实证据必须来自真实 DeepSeek 调用：`run_start.model` 有值、`mode=real`；不得把 mock 结果改名冒充真实运行。
4. 同一组的 `output` 与 `trace` 必须属于同一次运行（`run_id`、`status`、预算一致）；不允许跨运行拼接。
5. 用例实际结果与预期分支不符时，保留真实结果并在 README 说明，**不得为满足验收条件重写证据**；由需求方评审决定是否需要重新运行。
6. `tests/test_examples_evidence.py`（marker `evidence`）自动核对结构一致性（存在性、mode/run_id/status、trace 首尾事件）并复跑全部离线 command 对比归档 output；`model_response.response_id` 必须存在（真实为 DeepSeek 响应 ID，离线为合成 ID）。**默认执行，缺文件/不一致/空 examples 一律 fail，不允许 skip**；真实性依赖上述流程约束、需求方按 `docs/03` 复跑比对与人工抽查，测试不假装能验证真实性。

#### 15.4.5 正式真实模式"决策证据"（三条刺激必然发生的场景）

正式真实 trace 固定取以下三条，`README` 与 `docs/03` 标注"决策证据"；AC-05 real、AC-08a real 只作加分证据，不作唯一依赖：

| 场景 | 证据目录 | 必然发生的刺激 | 真实模式判定 |
| --- | --- | --- | --- |
| AC-03a | `examples/03_no_result/a_no_hit/real/` | 检索连续 0 结果 | 连续 0 结果后有可见反应（改写查询 → `ask_user`/`needs_input`；或明确 `failed` 说明检索持续无结果），全程无编造；与同场景离线 trace 对照 |
| AC-08b no_hit | `examples/09_controlled_retrieval/no_hit/real/` | 检索连续 0 结果 | 与 `hit/real` 对照：查询词、`total_matched`、工具调用序列/状态不同；判定同 01 §7.2 |
| AC-04a | `examples/04_tool_failure/a_recoverable/real/` | 注入 `search_materials=timeout@1` 必失败 | 观察到"失败 → 重试/调整"，`tool_errors` 含注入记录，最终不把失败当成功；与无注入 trace 对照动作不同 |

- 三条证据的判定以 trace 中可观察的 `tool_calls`、查询词/参数、`total_matched`、`tool_errors`、最终状态为准，不做文案比对。期望查询序列（写入对应 README 并锁定）：AC-03a `火星旅拍攻略 → 火星 → 星旅 → 旅拍 → 星河旅拍指南`；AC-08b no_hit `字幕功能上线 → 字幕 → 幕功 → 功能 → 星河旅拍指南`；全部 0 命中后 `ask_user`。
- 正式证据必须真实运行；离线固定策略的同场景 trace 可作对照，但不能替代真实 trace。
- 真实结果与预期分支不符时保留原样并在 README 说明，禁止补写或重造。

## 16. 实施顺序（已完成，按提交拆分）

| 序 | 模块 | 提交（测试通过后） | 关键验收 |
| --- | --- | --- | --- |
| 1 | `data/` 合成数据 + `dataset.py` | `feat: 合成素材与规则数据集` | 加载/校验/digest 测试通过；P01 单事实 |
| 2 | `domain/` + 时长/规则纯函数 | `feat: 时长估算与规则检查工具` | 公式、规则、引用校验测试通过 |
| 3 | 素材检索 + `tools/registry.py` | `feat: 素材检索与工具注册表` | 检索排序、注入、参数校验测试通过 |
| 4 | `llm/base + deepseek + mock` | `feat: DeepSeek 统一接口与离线 mock` | 真实接口字段处理单测（mock HTTP 响应）、离线策略测试 |
| 5 | `agent/state + trace + prompt` | `feat: Agent 状态与 trace` | trace 脱敏/禁止 reasoning_content 测试 |
| 6 | `agent/nodes + graph + runner` | `feat: LangGraph 编排与最终校验` | 离线场景矩阵通过 |
| 7 | `cli.py` | `feat: CLI 入口与状态输出` | 端到端退出码/JSON 测试 |
| 8 | 真实模式跑通五个验收场景 + `examples/` 证据 | `test: 验收场景证据` | DoD、五场景真实 trace、AC-05/AC-06 判定 |
| 9 | 核心用例与受控对照证据 + 证据一致性测试 + `docs/03–05`、README | `test: 受控对照与证据校验` / `docs: 评估与 AI 使用记录` | AC-05 核心 38→28、AC-08 两组对照、证据来源约束落地 |

收尾（已完成）：`docs/03` 评估、`docs/04` AI 使用、`docs/05` 复盘、`README.md`、真实证据刷新与终审；真实模型多轮稳定性抽测未做（见 `docs/05` §4）。

## 17. 风险与限制

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 语义级事实误用 | 有引用但张冠李戴无法自动判定 | 提示词强约束 + 逐字引用校验 + 人工评审；文档明确边界 |
| 真实模型不稳定 | 同一输入可能超时长/漏引用，验收不可复现 | 离线固定策略保证流程可复现；真实验收只断言硬约束；失败可重跑，证据保留实际结果 |
| AC-05 核心用例在真实模式首稿不可控 | 真实模型可能首稿直接 ≤30 或不触发压缩，无法用真实 trace 复现 38→28 | 正式核心证据用离线确定性 38→28（明确标注流程证据）；AC-05 real 只作加分证据，真实模式只用通用区间（27–33 即 `completed`，不因未压到 ≤30 判 failed），实际值如实记录；禁止补写 trace |
| 受控对照在真实模式未出现预期动作差异 | 真实模型两次都一次成稿，无法证明"动作随输入改变" | 真实对照（AC-08a）只作加分证据、不硬性要求方向差异；正式真实决策证据改用刺激必然发生的三条场景（§15.4.5）；实际差异如实填表 |
| 证据被质疑手工伪造 | 验收可信度下降 | trace 来源约束（§15.4.4）+ `test_examples_evidence.py` 全量复跑离线命令并逐项比对 + 真实 trace 记录 `response_id`；不得改名/拼接/补写 |
| 时长公式是近似 | 4 字/秒、5 字/秒与真实语速有偏差 | 常量集中、版本化 `duration-v1`；需求确认后可调并同步测试/文档 |
| 平台规则为合成数据 | 不能当真实合规依据 | README/文档显著标注；规则可替换为内部规范后重跑 |
| 思考模式 token 消耗 | `max_tokens` 被 reasoning 占用，工具调用被截断 | `max_tokens=4096` 上限 + 截断检测（`finish_reason=="length"` 时按协议错误重试/失败，不静默） |
| 单脚本多次提交导致 token 膨胀 | 步数/成本上升 | 步数 8、工具 16 上限；脚本长度 schema 限制；trace 只记必要字段 |
| 密钥误入产物 | 安全事故 | `.env.example` 占位符、trace 主动脱敏、测试断言、提交前扫描 |
| Windows 编码 | 中文乱码/写文件失败 | 所有读写 `encoding="utf-8"`、`PYTHONUTF8=1`、JSON 无 BOM |
| LangGraph API 变化 | 升级破坏 | 精确锁 1.2.18；升级需重跑全量测试并更新文档 |
| 无持久化 | 进程中断丢运行态 | 单次运行可重跑；trace 实时 flush，已有证据不丢；不做恢复 |

## 18. 需求方拍板记录（2026-10-28）

需求方已确认设计并冻结：Q1 ±10% 容差定稿（AC-05 真实模式只用通用区间，38→28 为离线参考实例核心证据）；Q2 四平台定稿，规则集保持"禁用词 + 时长上限 + 夸大表述 + 事实支撑"，平台上限标注合成，并要求独立测试证明"效率提升 80%"无引用必判 `CITE-001` error、finalize 不得 `completed`；Q3 引用口径定稿（语义误用人工兜底）。正式真实模式决策证据固定为 AC-03a / AC-08b no_hit / AC-04a 三条刺激必然场景（§15.4.5）。实现与终审已完成，本文档冻结。
