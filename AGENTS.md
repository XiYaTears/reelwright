# AGENTS.md

本仓库的工程契约。接手前先读完再动手。需求变更先改文档，再改代码。

## 1. 项目

短视频脚本 Agent：创作者给出主题、受众、平台、目标时长和风格，Agent 自主检索产品素材、生成分镜脚本、估算时长、检查品牌与平台规则，产出有事实依据、满足时长限制、可直接编辑的脚本。所有产品事实必须能追溯到素材库，禁止编造。

### 1.1 技术栈（已批准选型）

- Python 3.13.5 + 项目内 `.venv`；依赖锁定见 `requirements.txt`
- 编排：LangGraph `==1.2.18` 显式 `StateGraph`（agent / tools / finalize 三节点），不使用 `create_react_agent`、`ToolNode` 等预构建件
- 模型调用：httpx 直连 DeepSeek `/chat/completions`，统一 LLM 接口，真实与离线 mock 同接口替换
- 测试：pytest，离线模式零网络零密钥
- 框架只承担编排骨架；业务校验、预算、trace 由本项目实现，取舍与改动写入 `docs/02-solution.md`

## 2. 目录职责

- `src/`：Agent 编排、模型接入、工具、业务状态与 CLI
- `data/`：合成素材、品牌与平台规则（全部合成，禁止真实数据）
- `tests/`：可一条命令跑完的测试
- `examples/`：输入、实际输出、运行 trace、测试结果（证据材料）
- `docs/`：01 需求、02 设计、03 评估、04 AI 使用、05 复盘
- `README.md`：环境、安装、运行、评审入口
- `.env.local`：本地密钥，已 gitignore；禁止提交、打印、复制进任何文件或日志

## 3. 运行与模式

- 真实模式：读取项目根 `.env.local` 的 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`
- 离线模式：`--offline`，用 mock 决策跑通完整流程；mock 只验证流程，不证明模型决策质量，文档必须标明
- 统一 CLI 入口，命令写进 README；不做交互式输入
- Windows：执行前设 `PYTHONUTF8=1`；所有文件读写显式 `encoding="utf-8"`

## 4. 完成定义（DoD）

模块算完成，必须同时满足：

1. 真实入口能跑通，不是壳实现；
2. 有测试，断言行为而不是断言日志；
3. 真实模型跑出可读 trace；
4. 相关文档同步且与实现一致。

禁止占位实现、TODO 空壳、把 mock 结果描述成真实模型结果。

## 5. 模型接入

- 所有模型调用经统一 LLM 接口；真实实现与离线 mock 同接口替换
- 结构化动作走原生 tool calling，不自造 JSON 解析协议
- 结束条件必须可判定：显式 `finish` 工具或无 tool_calls
- 步数与工具调用次数上限由编排层强制；触顶返回明确的未完成状态
- 每步落盘 trace：步骤序号、动作（工具名与参数）、观察结果摘要、下一步决策依据（简短，不记录模型内部思维过程）

DeepSeek 接入实测要点：

- `POST {base_url}/chat/completions`，`Authorization: Bearer <key>`，OpenAI 风格 tools
- 响应 `choices[0].message.tool_calls[].function.{name,arguments}`，arguments 是 JSON 字符串
- 一次回复可能包含多个 tool_calls，必须全部执行并逐条回填 `role=tool` 消息
- 思考模式会返回 `reasoning_content`：不输出、不进 trace；reasoning tokens 计入 completion，`max_tokens` 要给足
- 思考模式下 `tool_choice` 只能 auto/none；需要强制工具时传 `thinking: {"type": "disabled"}`
- 单步延迟约 1–3 秒，按 8 步上限设计

## 6. 业务硬约束

- 事实可追溯：脚本里每一条产品事实、卖点、参数必须引用素材库中的素材 ID 与原文片段；素材中不存在的事实、效果数据、认证一律不得写入
- 时长：预计总时长必须落在目标时长的允许区间内（容差在设计文档中定义并写死）；超限必须压缩并重新估算，仍不满足则明确说明无法满足
- 规则：品牌与平台规则检查必须真实执行；命中违禁词、夸大表述、平台限制时修正或标为风险
- 检索无结果：由模型决定改写查询继续检索，或向用户请求补充信息（`needs_input`），不得静默编造
- 工具失败：不得掩盖；调整动作重试、换方案，或返回明确失败/未完成状态
- 输出可编辑：标题、分镜列表（每镜含画面、旁白/字幕、时长）、预计总时长、素材引用、风险、任务状态，结构化输出
- 状态必须明确：`completed` / `needs_input` / `incomplete` / `failed`

## 7. 证据规范

`examples/` 至少覆盖：正常生成、缺关键信息、素材检索无结果、工具失败、超时长修正（含无法满足的明确失败）。
每组用例包含：输入、实际输出、trace 文件、复跑命令；测试结果存 `examples/`。文档必须引用 examples 下具体文件。trace 必须来自实际运行。

## 8. 文档归属

- `docs/01–03` 由开发方维护
- `docs/04–05` 由开发方出初稿（只列事实，不要美化），需求方改写定稿
- `README.md` 由开发方写，需求方终审

## 9. 提交规范

- 每个模块测试通过后提交一次；message 格式：`feat|fix|test|docs|chore: 中文简述`
- 不提交密钥、`.venv/`、`__pycache__/`、`.pytest_cache/`
- `git init` 与提交由开发方执行；推送由需求方处理

## 10. 协作

- 需求有歧义先问，一次最多 3 个问题，不猜
- 需求变更先改文档，再改代码
- 写完 spec 与设计先停下，等需求方确认再实现
- 上下文出现矛盾（前后不一致、反复改同一处）时，主动提议另起干净会话交叉验证
- `AGENTS.md` 由需求方维护，发现问题在汇报里提，不要直接改
