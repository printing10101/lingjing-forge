# 黄金案例 #1（A2）：DXF → NC 全链路可复现基线

日期：2026-11-14。状态：**已跑通并入库**（8 段，6.88 s，单命令复现）；2026-11-15 修正基准面包络缺陷后重跑。产物目录 `tests/golden/case_01/`。

## 零件与判定

| 项 | 值 |
|---|---|
| 输入 DXF | `data/test_fixtures/case11_perforated_plate.dxf`（100×100×10 mm 板，25×Ø6 通孔） |
| 特征 | 25 孔 / 26 轮廓环 / 外轮廓 4 顶点（0–100 方形） |
| STEP 几何 | 体积 92,932 mm³ 量级，拉伸 − 25 孔布尔，偏差 <0.01% |
| 切削参数 | 25 条推荐，全部带 `stability_evidence`（status=ok） |
| 颤振 | 取最小裕度特征写入 `chatter_report.json`（Tlusty 1-DOF 绝对包络） |
| G-code | 624 行，`is_valid=True`，控制器 `fanuc_0i` |
| 体素校验 | **0 碰撞**，去除 52,103 / 195,000 体素，severity=none |

## 链路与产物

| # | 阶段 | 真实模块 | 产物 |
|---|---|---|---|
| 1 | DXF 解析 | `app.dxf.dxf_parser.DxfParser`（ezdxf） | `dxf_parse.json` |
| 2 | 特征提取 | `app.dxf.feature_extractor.FeatureExtractor` | `features.json` |
| 3 | STEP 建模 | CadQuery/OCP：外轮廓拉伸 − 孔布尔 | `model.step` |
| 4 | 切削参数 | `CuttingParamRecommender`（材料库 `steel_45` + 稳定性证据） | `cutting_params.json` |
| 5 | 颤振 | `app.simulation.chatter.stability`（经证据链路） | `chatter_report.json` |
| 6 | G-code | `ProcessPlanningPipeline`（孔识别→工序规划→后处理→语法校验） | `program.nc`、`pipeline.json` |
| 7 | 体素校验 | `VoxelValidator`（去毛坯仿真 + 碰撞判定） | `voxel_report.json` |
| 8 | 指纹 | SHA256 + 阶段耗时 + 环境 | `manifest.json`、`run.log` |

## 复现（仓库根，单命令）

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' `
    engineering/python/scripts/golden_case_01.py --force
```

回归测试（不依赖网络/LLM）：

```powershell
& '...Python314/python.exe' -m pytest engineering/python/tests/e2e/test_golden_case_01.py -q
```

3 项测试：端到端通过 + 幂等拒绝覆盖 + 确定性（程序体哈希一致、STEP 按体积比对）。

## 口径与已知限制

1. **通孔超程**：代码内 `_DRILL_BREAKTHROUGH_MM = 3.0`（工业常规 2–3 mm），体素校验以 `breakthrough_tolerance_mm=3.0` 显式放行。这不是放宽碰撞判定——其余任何越界仍判 `critical`（案例实测：不加容差时 150 处 collision，Z 至 −3.0 mm）。
2. **STEP 字节不可比**：OCP 导出头含生成时间戳，跨运行哈希不同；确定性以几何体积与程序体哈希衡量。
3. **孔深来源**：DXF 推断 `depth=25` 属特征提取默认值，本案例显式改为板厚 10（通孔），避免下游孔深收敛告警。
4. **单案例**：本文件只是案例 #1；C2 要求的另外 2 个真实零件仍需补（不同特征组合：型腔/凸台/曲面）。

## 验收

- 脚本 `engineering/python/scripts/golden_case_01.py`；产物 8 个 + `run.log`；manifest 含全部 SHA256。
- e2e 测试 5 项通过（`tests/e2e/test_golden_case_01.py`，含 3 个几何案例）。
- 断链即失败：任一阶段抛错则命令返回非零，不产出 `manifest.json`（fail-closed）。

## 案例 02 / 03（C2 几何覆盖）

同一脚本 + 不同 DXF，验证链路对非矩形外轮廓与多孔分布的通用性：

| 案例 | 输入 | 孔 | STEP 体积 | 体素校验 | 耗时 |
|---|---|---:|---:|---|---:|
| case_02 | `case6_flange.dxf`（200×200×10 法兰，圆形/台阶轮廓） | 7 | 272,925 mm³ | 0 碰撞，去除 100,686 | 6.7 s |
| case_03 | `case18_sprocket.dxf`（180×180×10 链轮，齿形轮廓） | 9 | 232,129 mm³ | 0 碰撞，去除 101,341 | 6.8 s |

复现：

```bash
PY='C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe'
$PY engineering/python/scripts/golden_case_01.py \
  --dxf data/test_fixtures/case6_flange.dxf --out-dir tests/golden/case_02 \
  --case-name case_02_flange_200x200_7holes --force
$PY engineering/python/scripts/golden_case_01.py \
  --dxf data/test_fixtures/case18_sprocket.dxf --out-dir tests/golden/case_03 \
  --case-name case_03_sprocket_180x180_9holes --force
```

**诚实边界（C2 未完全关闭）**：三例均为仓库自带测试图纸（`data/test_fixtures`），
不是客户真实零件。C2 要求「≥3 个真实零件 + 零件来源说明」，其中"真实来源"
仍需外部图纸（提供 DXF/STEP 后可直接复用本脚本产出案例 04+）。

几何预期已从包围盒改为**外轮廓多边形面积**（鞋带公式），非矩形件体积校验同样有效（偏差 <0.01%）。

## 修正记录（2026-11-15）

首版把 `part_description` 里的 `overall_dimensions` 留空，`ProcessPlanningPipeline`
于是回退到硬编码的 **200×100 基准面包络**：程序 X 最大到 195，实际切到工件外
（体素只统计毛坯内去除，故未报警，属静默越界）。修正后显式传真实包络，
程序 X/Y 回到 0–95，去除量由 99,354 降为 52,103 体素。

同一缺陷影响 `freeze_postprocessor_goldens.py` 与迁移验证，均已同步修正；
国产黄金集因此在 2026-11-15 重新冻结。
