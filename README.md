# 短视频脚本 Agent

面向短视频创作者的脚本助手（内部试用）：给主题、受众、平台、目标时长、风格，Agent 自主检索合成素材、
生成分镜脚本、确定性估算时长、检查品牌/平台规则，产出**事实可追溯、时长可卡住、可直接编辑**的脚本。

- 编排：LangGraph `1.2.18` 显式 `StateGraph`（agent / tools / finalize 三节点）
- 模型：DeepSeek `deepseek-flash`（httpx 直连 OpenAI 风格 tool calling）；`--offline` 用固定策略 mock
- 数据：`data/` 全部为合成虚构数据，P01 固定只含一条事实"支持字幕"
- 约束与设计：`AGENTS.md`、`docs/01-requirements.md`、`docs/02-solution.md`

## 环境

Windows + Python 3.13.5（项目内 `.venv`）：

```powershell
$env:PYTHONUTF8=1
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置（仅真实模式）

```powershell
Copy-Item .env.example .env.local
# 编辑 .env.local：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
```

- `.env.local` 已被 gitignore，禁止提交/打印/复制进任何文件或日志；trace 写出前会主动脱敏。
- `LLM_BASE_URL` 口径：含 `/v1` 的 OpenAI 兼容根；客户端会自动去掉尾部 `/`、缺 `/v1` 时补 `/v1`，
  因此写成 `https://api.deepseek.com` 或 `https://api.deepseek.com/v1` 均可。
- `LLM_PROXY` 可选：客户端 `trust_env=False`，不读系统 `HTTP(S)_PROXY`/`NO_PROXY`（本机
  `NO_PROXY` 含 `[::1]` 会触发 httpx 非法 URL）；需要代理时显式填写，如 `http://127.0.0.1:7897`。

## 运行

```powershell
$env:PYTHONUTF8=1
# 离线模式（无需密钥/网络）
.\.venv\Scripts\python.exe -m src.cli run --input examples/01_normal/input.json --offline `
    --out runs/demo/output.json --trace runs/demo/trace.jsonl

# 真实模式
.\.venv\Scripts\python.exe -m src.cli run --input examples/01_normal/input.json `
    --out runs/demo/output.json --trace runs/demo/trace.jsonl

# CLI 参数（非交互）
.\.venv\Scripts\python.exe -m src.cli run --topic "字幕功能上线" --audience "剪辑新手" `
    --platform douyin --target-duration 30 --style "轻快教程" --offline
```

可选参数：`--out` / `--trace`、`--max-steps`（1–20，默认 8）、`--inject-failure TOOL=CODE[@N]`
（CODE：`timeout|unavailable|invalid_response`）、`--data-dir`（受控实验）、`--env-file`。

退出码：`0 completed`、`3 needs_input`、`4 incomplete`、`5 failed`、`1` 内部错误、`2` CLI 用法/JSON 错误。

## 输出

stdout 与 `--out` 文件为同一份 JSON：`status`、`script`（标题/分镜/逐镜时长/总时长/引用/风险）、
`checks`（finalize 独立复算的估算与规则结果）、`needs_input` / `failure`、`tool_errors`、`budget`、
`data_digest`、`trace_path`。四状态：`completed` / `needs_input` / `incomplete` / `failed`。

trace 为 JSONL：`run_start`、`input_validation`、`model_response`、`tool_result`、`finalize`、`run_end`；
不记录 `reasoning_content`。`model_response` 中 `decision_note` 来自模型可见 content（可能 missing），
`decision_summary`（source=orchestrator）是编排层按实际观察+实际调用拼出的事实摘要，不是模型输出。

## 测试

```powershell
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe -m pytest -q            # 离线 + examples 证据一致性，无需网络/密钥
.\.venv\Scripts\python.exe -m pytest -q -m real    # 真实模型冒烟（需 .env.local）
```

当前结果：离线 `198 passed, 3 deselected`；真实冒烟 `3 passed`。结果文件见
`examples/pytest-offline.txt`、`examples/pytest-real.txt`。

## 验收证据（examples/）

- 正常生成：`examples/01_normal/`（offline + real）
- 缺关键信息：`examples/02_missing_input/`
- 检索无结果：`examples/03_no_result/`（a 持续无结果 / b 改写命中）
- 工具失败：`examples/04_tool_failure/`（a 可恢复 / b 持续失败）
- 超时长修正：`examples/05_over_duration/`（离线核心 38→28；真实 36→30 加分证据）
- 无法满足：`examples/06_unsatisfiable/`、步数上限 `examples/07_step_limit/`
- 受控对照：`examples/08_controlled_target/`（只改目标 30/60）、
  `examples/09_controlled_retrieval/`（只改检索结果，含离线与真实对照）

每组含 `input.json`、`command.*.txt`、`output.*.json`、`trace.*.jsonl`、`README.md`；所有 trace 均为
CLI 实际运行产物，禁止手写/补写。离线证据只证明流程与硬约束，不证明模型决策质量。

## 目录

```
src/cli.py           CLI 入口
src/agent/           LangGraph 编排（state/trace/prompt/nodes/graph/runner）
src/llm/             统一 LLM 接口（base/deepseek/mock）
src/tools/           素材检索、时长估算、规则检查、工具注册表
src/domain/          脚本/估算/规则模型与状态枚举
src/dataset.py       合成数据加载校验 + data_digest
data/                合成素材与规则
tests/               单元/集成/CLI/真实冒烟/证据一致性
examples/            验收证据与测试结果
docs/                需求、设计、评估、AI 使用、复盘
```

## 已知限制

- 素材、品牌/平台规则均为合成数据，不能作为真实合规依据。
- 时长为确定性近似公式（旁白 4 字/秒、字幕 5 字/秒、单镜 ≥2 秒、容差 ±10%），不是真实语速测量。
- 事实检查做"素材/事实存在 + 原文逐字 + 数字/资质类表述需引用支撑"的自动校验；语义级误用
  （有引用但张冠李戴）依赖人工评审。
- 真实模型输出非确定；离线 mock 仅验证流程。详见 `docs/03-evaluation.md` 与 `docs/05-retrospective.md`。
