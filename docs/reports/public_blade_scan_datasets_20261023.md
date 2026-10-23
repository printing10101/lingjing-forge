# 公开叶片扫描数据：检索结果与获取路径（2026-10-23）

**结论**：能同时满足「真实实测 + 双侧叶面点云 + 名义 CAD」的公开叶片数据只有一处——斯图加特大学 WINSENT 风电叶片激光扫描（3 支整叶 + IGES/STL），但**全部文件受限**，须以学术机构身份申请。国内最接近的一条是华中科技大学无锡研究院的「风电叶片测量点云数据」（完全共享），但需**下载申请表、签字盖章**。其余公开数据要么只有派生参数，要么只有单面/2D 轨迹，无法支撑位姿可辨识性检验。

## 一、候选清单（已逐条核实，含实测否决）

| # | 数据集 | 本体 | 实测? | 点云形态 | 名义 CAD | 许可 | 获取成本 | 判定 |
|---|---|---|---|---|---|---|---|---|
| 1 | **WINSENT 风电叶片激光扫描**<br>DaRUS `10.18419/DARUS-3859`（v5） | 750 kW 机组 3 支整叶（008/022/025，叶长约 15 m 级） | ✅ Leica 激光扫描，PS/SS 与 LE/TE 分侧 | 原始 `Friday_S*`/`Monday_S*`（.txt+强度，tar.bz2，1.9–40 MB/份）+ 预处理合并 `.bin`（221–458 MB/支） | ✅ IGES / STL / Plot3D | CC BY 4.0 | **全部 `restricted`**：需学术机构账号登录 Request Access，或邮件联系作者 | **首选**，须申请 |
| 2 | **风电叶片测量点云数据**<br>NBSDC `CSTR:16666.11.NBSDC.KODD7MJW` | 大型风电叶片**截取段** | ✅ 华科无锡研究院，2020-07，武汉国家创新制造中心，机器人激光测量系统 | 未公开说明格式（申请后可见） | 未说明 | 完全共享 | 注册 + 申请表 + **签字盖章** + 审批 | **国内首选**，须盖章 |
| 3 | **叶轮缺陷零件点云**<br>NBSDC `CSTR:16666.11.NBSDC.MXPtx9La` | 航空叶轮（缺陷件） | ✅ 三维激光扫描仪（航空研究所提供） | 未公开说明 | 未说明 | 完全共享 | 同上 | 备选（叶轮非叶片） |
| 4 | **压气机转子叶片加工偏差**<br>Sci Data 13, 462 (2026)，数据 `10.57760/sciencedb.27122` | 100 支真实压气机叶片 | ✅ CMM 逐截面测量 | ❌ **只发布派生参数**（13 截面 × 7 类偏差 = 9,100 个数值），无原始点云 | 无 | 开放（CC BY-NC-ND 4.0） | 直接下载 | **不可用于位姿检验**（无点云）；可作偏差统计先验 |
| 5 | **GM 涡轮叶片立体点跟踪**<br>figshare `10.25403/UPresearchdata.26062732` | 五叶转子上的 3 支叶片 | ✅ 双高速相机 4000 fps | ❌ 实测为**每帧 2D 像素坐标**（xlsx，5,177 帧 × 38 点），无标定文件无法三角化 | 无 | CC BY 4.0 | 直接下载（17 MB，已下载核实） | **已否决**（下不到 3D） |
| 6 | **GGG SfM 噪声分割**<br>Mendeley `10.17632/xtv5y29xvz.2` | 15 个重建对象，含 1 段风电叶片 | ✅ 单反 SfM 重建（Agisoft） | 顶点 XYZ+RGB、法线、三角面、噪声/非噪声标注 | 无 | CC BY 4.0 | 5.69 GB | 单侧叶面补丁，**做不了压/吸力面翻转**；可用于真实噪声建模 |
| 7 | **风电塔筒激光扫描**<br>figshare `10.6084/m9.figshare.28903136` | 海上风电支撑塔筒 | ✅ Leica P40，6 个站位 | 6 个 `.pts`（xyz） | 无（旋成体） | CC BY 4.0 | 6.1 GB 单包 | **立即可下**，但非叶片；可作"实测对称体"对照 |
| 8 | ENOLA 螺旋桨数据库<br>Zenodo `10.5281/zenodo.20111572` | 多族螺旋桨 | ❌ 几何为 CAD | 无扫描点云 | ✅ | 开放 | 223 MB | 只能当名义模型 |
| 9 | GitHub 公开 `.ply/.pcd` 叶片 | — | ❌ | 检索 `filename:blade.ply` 命中 9 个，全为游戏资产；`blade.pcd` 仅 1 个（CAD 导出） | — | — | — | **已否决** |

## 二、已实验证：pipeline 已就绪，数据一到即跑

新增 `identifiability_probe.py --mode real`：不需要名义 CAD，也不需要已知变换。

- 在点云自身 PCA 框架下，绕三条主轴各做 180° 翻转，算 `翻转残差 / 点间距`；
- `比值 ≈ 1` → 翻转把曲面映回自身 → 该轴上位姿**不可辨**；`显著 > 1` → 可辨；
- 分辨率上限 = 点间距（小于间距的不对称这个检验看不见，须退回 Δ/σ 的统计路线）。

**往返验证**（把合成叶片导出成 `.pts` 再当外部点云喂进去，20k 点、0.02 mm 噪声）：

| 点云 | 点间距 | 长轴 | 中轴（**弦向/翻转轴**） | 薄轴 | 判定 |
|---|---|---|---|---|---|
| 对称 NACA0012 | 0.234 mm | 3.41 | **1.12 → 自对称** | 3.40 | 不可辨（与解析结论一致） |
| 带弯度 NACA2412 | 0.237 mm | 3.64 | **1.98 → 可辨** | 3.38 | 可辨（但仅 2× 间距，接近阈值） |

第二行顺带说明一件事：2% 弯度（0.8 mm）在 0.24 mm 点间距下只有 2 倍余量——**真实扫描若要判弯度歧义，点间距必须显著小于弯度差**。这条对"该扫多密"是可直接用的约束。

## 三、需要你出面的一步（我不代发、不代签）

**路线 A（首选，WINSENT）— 邮件申请**。模板如下，你补机构与用途后发 `thorsten.lutz@iag.uni-stuttgart.de`（或走 DaRUS "Contact Owner"）：

> Subject: Access request — WINSENTvalid blade scan data (DOI 10.18419/DARUS-3859)
>
> Dear Dr. Lutz,
>
> I am Yulong Li, an undergraduate researcher at Guizhou University (Guiyang, China), working on pose identifiability of blade point-cloud registration — specifically, when a blade's pressure/suction symmetry makes the registration pose unidentifiable, and how much supplementary scanning is needed to resolve it.
>
> I would like to request access to the following files of your dataset (DOI 10.18419/DARUS-3859):
> - `Friday_S1_clean.txt.tar.bz2` … and the corresponding `Monday_S*` edge scans for one blade
> - `Blade008_closedTE.stl` and `Blade008_periodicSpline.iges` (nominal geometry)
>
> Intended use: non-commercial academic research; we will not redistribute the raw data, and will cite your dataset and the JPCS 2767 042002 paper in any output. If a smaller subset is preferable, the preprocessed `Blade008_inputCloudsForAutomaticProcessing.bin` alone would be sufficient.
>
> Thank you for maintaining this dataset.
> Yulong Li — ORCID 0009-0006-0492-857X

**路线 B（国内，走盖章）— NBSDC 申请**。步骤：注册 `nbsdc.cn` → 检索 CSTR `16666.11.NBSDC.KODD7MJW` → 下载申请表 → 填"课题名称：叶片点云配准位姿可辨识性与补扫规划""用途：非商业学术研究""不对外转发原始数据" → 负责人签字 + 学院公章 → 上传等审批。申请表里的技术摘要可以直接引用 `docs/reports/identifiability_pointcloud_20261023.md` 的结论段。

## 四、如果两条都等不到，还有什么能做

1. **先用塔筒（#7）做实测对照**：6.1 GB，CC BY 4.0，立即可下。它是旋成体，理论上对绕轴的翻转自对称——若实测 `比值 ≈ 1`，就证明了"判据在真实激光扫描上成立"，只是本体不是叶片。可信度低于叶片，但比没有强。
2. **用 GGG（#6）做真实噪声建模**：5.69 GB，取出叶片段的重建点云，量它的噪声水平与空间相关性，替换合成实验里 iid 0.02 mm 的假设，重跑 baseline 看 D 是否稳健。
3. **合成侧继续加严**：把点间距、噪声相关性、遮挡率做成三维扫描参数，给出"什么扫描规格下该判据可用"的相图——这条完全不依赖外部数据，且是审稿人会问的第一个问题。

## 五、复现

```bash
cd research/prototypes/pointcloud_registration
PY="C:/Users/Lenovo/AppData/Local/Programs/Python/Python314/python.exe"

# 真实点云自检（支持 .pts/.txt/.xyz/.csv/.ply，多份用逗号分隔）
"$PY" identifiability_probe.py --mode real --cloud "data/external/symmetric_naca0012_probe.pts,data/external/cambered_naca2412_probe.pts"

# 全套原型测试（9 passed）
"$PY" -m pytest test_pca.py test_registration.py test_identifiability.py -q
```

产物：`artifacts/identifiability_real_20261023/results.json`。
`data/external/` 目前只放两份**合成** smoke 文件（文件名带 `_probe`）；真实外部数据到位后放同目录，勿与合成数据混名。
