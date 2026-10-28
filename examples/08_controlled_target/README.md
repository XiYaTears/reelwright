# AC-08a 受控对照：只改目标时长

| 组 | 首稿估算 | verdict | 改稿方向 | 最终总时长 | 状态 |
| --- | --- | --- | --- | --- | --- |
| target_30 | 38s | too_long | 压缩 | 28s | completed |
| target_60 | 38s | too_short | 扩张 | 58s | completed |

同一 38s 初稿在两组分别被判 too_long / too_short，下一步动作方向相反；两条 trace 均为实际运行产物。
`mode=offline`：这是确定性流程证据，不证明真实模型决策质量。
