# 归档：零切削 E2E 证据（2026-06-18 批次）

**这批产物不能用作端到端证据。** 2026-10-07 全库定级实测：下面 161 个 `.nc`
（`e2e_v2/` 8 控制器 × 20 fixture = 160，加 `test_shadow/` 1 个）里
**切削指令数为 0** ——只有程序头、`M03/M08`、程序尾：

```
%
O0001 (PROGRAM 1 - 2026-06-18)
(POST: Fanuc 0i-MF)
G21 G17 G40 G49 G80 G90
G00 G91 G28 Z0.
...
M03 S8000
M08
            ← 这里本该是刀轨，是空的
M09
M05
...
M30
%
```

复核命令（任一文件都行）：

```bash
grep -cE '^\s*G0?[123]\s+[XYZ]' e2e_v2/fanuc_0i/case18_sprocket/case18_sprocket.fanuc_0i.nc   # → 0
```

而同目录 `e2e_v2/e2e_v2_summary.json` 给每个案例都记了 `gcode_ok: true`、
`success: true`。那是阶段 6 的 `DxfProcessService._run_gcode` 还只写 header/footer
冒烟占位时期的产物——当时「端到端成功」只等于「调用没抛异常」。
该占位在 2026-09 被修掉（现在走真实 `ProcessPlanningPipeline`，
由 `tests/unit/test_dxf_process_service.py::test_gcode_stage_produces_real_program`
守住），但**旧语料和旧 summary 一直留在仓库里**，且生成它们的脚本此后被删/改名
（全仓已无任何代码引用 `e2e_v2`），所以既不能复现也没有被更新。

## 现在的有效证据在哪

- 语料：`data/outputs/e2e_v2/<controller>/<case>/<case>.<controller>.nc`
  ——由当前主路径重新生成，8 控制器 × 20 fixture = 160 个，全部含切削运动。
- 清单：`data/outputs/e2e_v2/e2e_v2_summary.json`，逐文件记录
  `cutting_moves` / `drill_cycles` / `strategies` / 耗时；`gcode_ok` 现在
  **要求确有切削运动**，不再等于"没抛异常"。
- 生成器：`scripts/regenerate_e2e_nc_corpus.py`（任一文件零切削即 exit 1）。
- 门禁：`engineering/python/tests/unit/test_output_corpus_hygiene.py`
  扫描 `data/outputs/**/*.nc`，出现零切削空壳即测试失败。

## 处置说明

- 本目录文件**保留不删**（可逆处置），但已脱离 `data/outputs/`，
  不会再被误当成当前 E2E 产物；`.stl`（同批 3D 模型输出）一并归档。
- 引用过这些路径的两份文档已改为指向新语料与生成器：
  `docs/operations/工业级交付路线图.md`、
  `docs/workshop_landing_preparation/P3-1-控制器兼容性验证.md`。
