# 平面刀轨引擎（2.5D Planar Toolpath Engine）设计说明

> 模块：`engineering/python/app/toolpath/planar_engine.py`
> 集成点：`app/process_planning/operation_sequencer.py`（几何注入 + 方法映射）、
> `app/process_planning/_feature_code_mixin.py`（铣削分支刀轨生成）
> 测试：`engineering/python/tests/unit/test_planar_toolpath_engine.py`

## 1. 背景与动机

升级前，铣削类工序的 G 代码生成存在"几何刀轨层缺失"问题：

- `cutting_params["geometry"]` 无任何上游注入（永远为空字典）；
- 铣削分支回退到模板走线：缺省 **10×10mm 矩形三边直线插补**，与零件真实轮廓无关；
- 挖槽（through_pocket/blind_pocket）与凸台特征的方法名落入"精加工-{type}"通用分支，
  **不生成任何 G 代码**。

本次升级补上了 2.5D 几何刀轨层：铣削 G 代码从真实轮廓计算刀心轨迹
（contour-parallel 环切 / 外形偏置 / 端面 raster），几何不可用时静默回退模板走线。

## 2. 能力范围（诚实边界）

**能做**（直线段简单多边形，无岛屿/内孔；圆弧由调用方离散化）：

| 策略 | 触发方法名（子串匹配） | 行为 |
|---|---|---|
| 挖槽环切 `pocket_contour_parallel` | 槽 / 腔 / 挖 | 外环贴壁→向内 stepover 环切，退化中线清底；Z 分层；斜坡下刀 |
| 外形偏置 `profile_offset` | 外形 / 轮廓 / 凸台 | 轮廓外侧单环偏置（刀具半径+精加工余量），Z 分层 |
| 端面 raster `face_raster` | 平面 / 端面 | 矩形区域蛇形布线，行间切削进给转入 |

**不做**（与顶尖 CAM 的差距所在，见 docs/产品叙事与战略对标）：

- 曲面 cutter-location 刀轨、5 轴联动刀轴场（`five_axis_planner.py` 仍为姿态点规划）；
- 机床运动学仿真与刀柄/夹具干涉（`simulation/kinematics/`、外部 CAM 校验职责）；
- 残余加工/摆线/等高精加工等高级策略——本引擎只保证"环间全覆盖 + 不过切"的
  2.5D 粗加工语义。生成的 G 代码仍须通过 `cam_validation` 流程方可上机。

## 3. 算法要点

1. **偏置**：多边形逐边沿法线偏置 d（正内负外），相邻偏置线求交；尖角 miter
   超过 `miter_limit*d` 截断。
2. **自交清理**：闭合折线在最早交叉点递归分解为简单环，按取向过滤——
   顺时针伪环丢弃、CCW 环保留（同时覆盖"偏置导致区域分裂"：每个子区域各自成环）。
   每个环保做偏置有效性校验（顶点+边中点到原轮廓距离 ≥ |d|−容差）。
3. **退化中线环**：偏置使区域退化为线段/折线（如 40×20 槽、φ10 刀、d=10）时
   保留为清底刀轨（面积≈0 但通过有效性校验）。
4. **环切终止**：d 从刀具半径起、按 stepover 递增，始终相对**原始轮廓**计算
   （无误差累积）；偏置无有效环即停。`stepover_ratio ≤ 1.0`（默认 0.5）保证环间全覆盖。
5. **斜坡下刀**：沿本环/本行以 `ramp_angle`（默认 3°）螺旋/拉锯下降，Z 随弧长线性
   递减；超过 `max_laps` 圈仍不足则退化为垂直下刀。开放路径逐圈翻转方向（真实折返）。
6. **覆盖保证的物理边界**：圆截面刀具铣不出方形内角——壁面半径过渡带必然残留。
   全覆盖判定仅针对"材料内部且距壁面 ≥ 刀具半径"的可加工区域（测试同此口径）。

## 4. 数据流

```
DXF/输入 → 特征识别(CavityFeature/BossFeature.to_machining_feature)
  → OperationSequencer.plan_operations
      ├─ _select_machining_method: pocket/boss 类型 → 粗/精铣挖槽、粗/精铣外形（进铣削分支）
      └─ _extract_feature_geometry: dimensions → cutting_params["geometry"]
           （x/y + anchor=center|corner、length/width/depth、orientation）
  → GCodeGenerator._generate_feature_code（铣削分支）
      └─ _generate_planar_milling_lines
           ├─ _milling_geometry: geometry → 策略 + 多边形/矩形
           ├─ PlanarToolpathEngine.pocket/profile/face_raster
           └─ 逐 move 输出：rapid → G00；plunge/cut → format_linear_move（方言适配）
任意一步失败 → logger.warning + 回退模板走线（G41/G42 语义不变）
```

`geometry["contour"]`（显式顶点列表）为可选扩展点，优先于 length/width 矩形合成，
供上游 DXF 轮廓/上游模块直接传入任意多边形。

## 5. G 代码语义

- 引擎输出为**刀心轨迹**：已按刀具半径偏置，**不再使用 G41/G42 刀具半径补偿**
  （补偿叠加会造成双重偏置）；模板走线回退路径保留原 G41 语义不变。
- 快移约束：XY 快移仅发生在 `stock_top_z + clearance`（默认 +2mm）平面；
  下刀一律 G01 斜坡/垂直进给，无 G00 入料。
- 进给：切削进给取切削参数库并经 ConfigLimiter 限幅；下刀进给 = 0.5× 切削进给。

## 6. 已知限制与后续方向

- 环间材料覆盖以 `stepover ≤ 刀具直径` 为前提；`stepover_ratio` 强制 ≤ 1.0。
- 无螺旋下刀（沿整圈等螺距下降）；当前为"整圈数均分深度的斜坡"，圈数由
  `max_laps`（默认 4）封顶。
- 偏置自交分解为 O(n²) 检测，轮廓顶点 > 2000 直接拒绝（`ToolpathScaleExceededError`）。
- 钻孔分支的 `geometry` 注入（position_x/y）会改变 `_generate_feature_code` 钻孔
  坐标来源（原为 0,0；真实孔位走 `_HoleDrillingMixin.generate_hole_program`，不受影响）。
- 后续：残余加工（rest machining）、摆线开粗、沿面 spiral、与体素切削仿真联动验证。
