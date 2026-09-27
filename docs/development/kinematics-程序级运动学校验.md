# 程序级运动学校验（Kinematics Validator）设计说明

> 模块：`engineering/python/app/simulation/kinematics/`（machine.py / interpreter.py / validator.py）
> 集成点：`app/cam_validation/stages/software_check.py`（阶段 7 步骤 3.5）、
> `app/dnc/nc_gate.py`（DNC 下发闸门）、`app/config/cam_validation.py`（开关）
> 测试：`engineering/python/tests/unit/test_kinematics_simulator.py`

## 1. 背景与定位

升级前 `simulation/kinematics/` 是空目录（仅 .gitkeep），G 代码在阶段 7 只有
AABB 碰撞预筛（CollisionDetector）与体素材料去除仿真（VoxelValidator）两道闸，
缺少 VERICUT/NCSIMUL 类软件的"程序级机床语义检查"子集：行程超限、主轴未转、
未设进给、快移扎刀、未装刀等——这些问题不需要几何仿真就能秒级发现。

## 2. 三道闸架构（阶段 7 现状）

```
G 代码 → ① 运动学校验（K001-K009，毫秒级，机床语义）
       → ② AABB 碰撞预筛（CollisionDetector，秒级）
       → ③ 体素材料去除仿真（VoxelValidator，秒-分钟级）
       → ④ 外部 CAM 二次校验（CamAdapter：NX/PowerMill/manual，强制）
每道闸失败都会写入 task.errors / 检查位；DNC 下发闸门要求全部通过。
```

边界（诚实声明）：本模块不做几何碰撞、不做刀柄/夹具干涉、不做多轴 RTCP
逆解（3 轴语义）。圆弧按 10° 弦差采样；G83/G73 啄式循环按孔底最低点检查
（中间抬落不改变包络）。

## 3. 检查项

| 代码 | 检查项 | 严重度 |
|---|---|---|
| K001 | 轴行程超限（所有运动采样点换算机床坐标后判定） | error |
| K002 | 主轴未运转执行切削（G1/G2/G3/孔底进给） | error |
| K003 | 切削移动未设定进给 F | error |
| K004 | 切削进给超过机床切削上限 | error |
| K005 | 主轴转速超过机床上限 | error |
| K006 | 快移(G00)扎入毛坯区 / 在毛坯顶面下方横移 | error |
| K007 | 未装刀（无 T..M06）执行切削 | error |
| K008 | 刀号超出刀库容量 | error |
| K009 | 程序缺 M30/M02 | warning |

## 4. 坐标语义（关键约定）

- 解释器内部全部为**程序坐标**；`机床坐标 = 程序坐标 + work_offset`。
- 机床画像来自 `app/database/data/machines.json`（`travel_xyz_mm` → 轴限位
  [0, travel]，`spindle_speed_rpm` / `feed_*_mmmin` / `tool_changer_capacity`）；
  文件缺失或 id 未命中时回退内置 VMC-850 默认画像。
- **工件顶面在程序 Z0（钻深为负）是常见写法**，直接以 offset=(0,0,0) 校验会误报
  K001。管线集成使用 `auto_work_offset(profile, stock_top_z)`：把毛坯顶面安放到
  机床 Z 行程 40% 高度。真实生产应以对刀数据传入显式 work_offset 为准。

## 5. 解释器能力范围

- 模态：G0-G3 运动组、G17/18/19 平面（非 XY 平面圆弧退化为直线并告警）、
  G20/G21 单位（英制自动 ×25.4，进给同步换算）、G90/G91、G54-G59（非 54 告警）。
- 固定循环：G81/G83/G73/G85 + G98/G99，展开为 定位→R 面→孔底进给→退回 四段；
  同行多 G 代码（如 `G90 G81`）按列表解析不互相覆盖。
- 圆弧：I/J 圆心增量与 R 半径（含优/劣弧圆心判定）两种形式，10° 采样。
- 状态跟踪：M3/M4/M5 主轴、T+M6 装刀、F 进给、S 转速、M30/M02 结束。
- 未识别的 G/M 代码按"无运动"跳过，不产生误报。

## 6. 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LNN_CAM_KINEMATICS_ENABLED` | true | 关闭后闸门按"未知"拦截（实际不可跳过，仅供调试对照） |
| `LNN_CAM_KINEMATICS_MACHINE_ID` | vmc_850 | machines.json 中的机床画像 id |

## 7. 已知限制与后续方向

- 无刀具长度/半径的几何干涉（刀柄-夹具）检查——需装配体模型（VERICUT 级）。
- 无多轴运动学（A/C 轴行程、RTCP 逆解、奇异点）。
- 固定循环啄式中间段不逐段展开（包络等价）。
- 行程判定对**旋转/镜像装夹**（G68 等）未建模。
- 后续：与五轴后处理（xmachine_xm100）联动的 A/C 行程检查；机床画像接入
  ConfigLimiter 同源配置；前端校验报告展示 kinematics issues 分组。
