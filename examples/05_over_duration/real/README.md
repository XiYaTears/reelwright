# AC-05 real：加分证据（不作唯一依赖）

- 模式：`real`；命令见 `command.real.txt`
- 结果：`status=completed`，退出码 0；最终总时长 30s（区间 {'min_s': 27, 'max_s': 33}）
- 估算序列（总时长, verdict, within）：[(36, 'too_long', False), (30, 'within', True)]
- 首稿 36s > 30s，模型压缩后重估 30s 落在通用区间；方向与参考实例 38→28 一致但数值不同，属真实模型实际行为。
- `tool_errors`：[('submit_script', 'invalid_arguments')]（模型一次 submit 参数非法后自行纠正；如实保留）。
- 真实模式判定只用通用区间（≤30 记与参考一致，31–33 记容差内高于参考），本组为 30s。
