# NASA Milling 实测：参数信息跨组合检验

日期：2026-11-14。承接[公开数据调研](public_dataset_action_response_survey_20261114.md)。结论：**在 NASA Milling 上，DOC/进给/材料这组案例级参数对振动响应无可学的跨组合信息（R² −0.99），对磨损速率只有弱信号（R² +0.105）且不如先验磨损状态（+0.199）**。这不推翻"参数影响加工"的物理常识，而是说明该数据集 2 案例/组合的规模无法支撑跨组合泛化——正式检验必须换 JUST_CAS（115 段、四参数、真实三向力）。

## 实测核实的数据事实（审计）

`reports/nasa_milling_audit_20261114.json`（sha256 见结果文件）：

| 项 | 实测值 |
|---|---|
| 案例 / 走刀 | 16 / 167 |
| 参数组合 | **8 组**（材料 2 × DOC{0.75,1.5} × 进给{0.25,0.5}），每组 2 案例 |
| 主轴转速字段 | **不存在**（速度效应不可检验） |
| VB 覆盖 | 146/167 测得，21 条缺测；范围 0.00–1.53 mm |
| 信号长度 | 9000 点（166 条）与 15360 点（1 条），250 Hz |
| 最短案例 | case 6 仅 1 条走刀 |

结论：**装不进现有 history=20 的窗口口径**（多数案例仅 6–17 条走刀），故改为走刀级回归检验。

## 检验设计

- 入口 `engineering/python/scripts/wm_external_action_probe.py`；留一参数组合（8 折），λ 由训练集内留一案例交叉验证选择，预测值裁剪到训练目标区间（小样本外推保护，未裁剪值单独记录）。
- **T_vib**（n=151）：以走刀内 `vib_spindle` RMS 为响应，状态特征=上一条走刀的振动/电流 RMS。
- **T_wear**（n=118）：以上一条测得 VB 到本条 VB 的增量为响应，状态特征=上一条 VB。

## 结果（R² 对训练折均值基线，裁剪后）

| 任务 | actions 档 | state 档 | state+actions |
|---|---:|---:|---:|
| T_vib（振动 RMS） | **−0.993** | +0.029 | −1.004 |
| T_wear（磨损增量） | +0.105 | **+0.199** | +0.107 |

- 振动：参数档远差于均值基线，说明跨组合的振动电平主要由**案例级因素**（刀具/装夹/传感器差异）主导；同案例内上一条走刀的信息也几乎无迁移（+0.029）。未裁剪时 state 档 R² 达 −1e62 量级，裁剪保护后仍无正收益，这一负结果不是数值噪声。
- 磨损速率：参数档弱正（+0.105），先验 VB 更强（+0.199），二者合并不增——参数携带的磨损速率信息与先验状态重叠，且被案例差异稀释。

## 判读与边界

1. 该数据的统计单元实际是 **16 个案例**，不是 167 条走刀；每组仅 2 案例，留出折的方差主要来自案例差异，参数效应被淹没。**不能据此否定参数效应**，只能说"该数据无法证明"。
2. 无转速字段 → 速度这一最关键的 CAM 参数完全缺席。
3. 振动信号为原始通道 RMS（非计量振级），跨案例增益未知。
4. 磨损增量来自稀疏 VB（21 条缺测），相邻对更少（118 对，其中部分跨缺测间隔）。
5. 结论为方向性判断，不进入任何对外表述。

## 下一步：JUST_CAS 夜间下载（444.1 MB，CC BY 4.0）

当前网络实测 ~18 KB/s，预计 7 小时左右；建议夜间执行（可断点续传，失败重试）：

```bash
mkdir -p data/external/just_cas
curl -sL -C - --retry 5 --retry-delay 10 \
  -o data/external/just_cas/JUST_CAS_milling-V0.zip \
  "https://zenodo.org/records/10613521/files/liuyinfei1030/JUST_CAS_milling-V0.zip?download=1"
```

到位后的首个实验：以 115 段为样本、四参数（切深/切宽/进给/转速）+材料为动作，三向力 RMS/振动 RMS 为响应，做**留参数组合**对照（actions vs state vs 合并），回答"参数信息是否可学"；这是当前唯一可能支撑"参数响应预测"叙事的公开数据检验。

## 文件与验收

- 新增 `wm_external_action_probe.py`，单测 4 项通过（`test_external_action_probe.py`）：VB 缺测识别、相邻对构造、动作信号检出与确定性、外推爆炸裁剪。
- 结构审计脚本与单测（3 项）见上一提交；审计结果 `reports/nasa_milling_audit_20261114.json`（本地）。
- 结果与数据指纹：`research/experiments/results/nasa_action_probe_20261114.json`。
- 原始数据不入库：`data/external/` 已加入 `.gitignore`；源与许可见调研报告。

## 复现（仓库根）

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' `
    engineering/python/scripts/wm_external_action_probe.py `
    --mat data/external/nasa_milling/extracted/inner/mill.mat `
    --out research/experiments/results/nasa_action_probe_new_run.json
```
