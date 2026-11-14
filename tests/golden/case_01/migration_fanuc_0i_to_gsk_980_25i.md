# 工艺迁移验证清单：fanuc_0i → gsk_980_25i

结论：PASS（容差 0.001 mm）

| 检查项 | 状态 | 说明 |
|---|---|---|
| total_segments | warn | source=312 target=313（含快速定位拓扑，差异仅供参考） |
| cutting_segment_count | ok | source=95 target=95 |
| cutting_motion_sequence | ok |  |
| cutting_endpoint_deviation | ok | 最大偏差 0.000000 mm（容差 0.001 mm） |
| arc_radius | ok | 圆弧 0 段，最大半径差 0.000000 mm |
| feed_rates | ok | source=[137.5, 275.0] target=[137.5, 275.0] |
| spindle_speeds | ok | source=[1150, 1850] target=[1150, 1850] |
| xy_positions | ok | source=63 点 target=63 点 |
| drill_cycles | ok | source=25 target=25 |
| dialect_codes | warn | 仅源: ['G5.1']；仅目标: ['G30'] |

## 方言差异（需人工确认）
- 仅源程序：G5.1
- 仅目标程序：G30

> 本方仅验证后处理输出语义，不替代空运行与首件试切。
