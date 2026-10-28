# AC-01 正常生成

## 离线证据（`mode=offline`，确定性）

- 命令：`command.offline.txt`；结果：`status=completed`，总时长 28s
- 离线只证明流程与硬约束，不证明真实模型决策质量。

## 真实模型证据（实际运行，未改写）

- 模式：`real`；命令：`command.real.txt`
- 结果：`status=completed`，退出码 0；总时长 30s（区间 {'min_s': 27, 'max_s': 33}），6 镜，引用素材 ['P01', 'P06', 'P02', 'P08']；规则 ok=True，tool_errors=0
- trace 的 `model_response.response_id` 均非空（DeepSeek 响应 ID）；`decision_note_source`：['model']
