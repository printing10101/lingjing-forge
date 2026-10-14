# I3 · C2 cutting_force_v2 训练结果：双口径如实报告

> 日期：2026-10-14 ｜ 对应 I2 §3/§4（训练 + parity + manifest v2 + 双口径评估）
> 定位：结果文档（D7 素材）。所有数字均为实测（manifest / numpy 直算），含未达标结论——**不粉饰**。

## 0. 一句话结论

**口径 B 未达标（R²=0.542 < 0.90），且实测证明达标线对该目标定义不可达**：纯工况特征是实验级常数，而窗口均力的方差主体是磨损漂移——信息论上限（用各实验真实均值当预测）只有 R²=0.411。按 I2 §4 纪律如实双报，不回退口径 A 充数。**这个"0.998（合成）→ 0.542（真实）"的落差本身就是 D8 防 reward hacking 叙事的正面素材。**

## 1. v1 / v2 对比表（D7 两列格式）

| 项 | v1（合成基线，保留不覆盖） | v2（真实数据，本轮） |
|---|---|---|
| 数据 | 100 行合成网格 | uniwear 实测力信号 39,903 行 → 1,578 窗口（window=50/step=25） |
| 特征 | spindle_rpm, feed_rate, depth_of_cut, material_encoded | spindle_rpm, **feed_per_tooth_mm**, axial_doc_mm, material_encoded（特征序重定义，I2 §2.3 同一原则） |
| 工况来源 | 合成网格（自造） | 公开文档回填（conditions_lookup.csv，每行带 source） |
| test R² | 0.998（合成测试集） | **0.385（口径 A） / 0.542（口径 B）** |
| MAE | — | 45.7 N（A） / 47.0 N（B） |
| MAPE | — | 108%（A） / 298%（B）——**对该目标不适用**（76 个近零力窗口，见 §3） |
| parity（torch vs NumPy 镜像） | 1e-3 内 | **3.0e-08（A） / 8.0e-09（B）** |
| 溯源 | dataset_store | csv + sha256 全长 + manifest notes 诚实声明 |
| 注册表 | cutting_force v2.0.0 | cutting_force_v2 v1.0.0（`models/lnn/cutting_force_v2.npz`，weights_source=trained，终验通过） |

## 2. 双口径结果与切分构成

| 口径 | 切分 | train/val/test（窗口数） | test 指标 |
|---|---|---|---|
| A（乐观上界） | 窗口级随机（split_random，seed 42） | 1105/237/236 | R²=0.385, MAE=45.7 |
| **B（达标口径）** | 按 experiment_tag 分组（split_group） | 1073/296/209（8+2 tag / 2 tag / 2 tag） | **R²=0.542, MAE=47.0** |

口径 B 的 test = 1 个 nuaa tag + 1 个 phm2010 tag；train 含两种数据集（material_encoded 11 与 12 均有出现）。

## 3. 差距来源解释（为什么 0.90 不可达）

1. **特征是 tag 常数，目标是磨损漂移的函数**：同一实验内工况恒定，但窗口均力 std 达 50–110N（约为均值的 50%+，如 W1：mean 135.7 / std 77.4）。条件特征原则上只能预测 tag 级均值。
2. **信息论上限实测**：以各实验真实均值当预测，全体 R²=0.411——任何纯工况模型都压不过这条线。口径 B test 上的 0.542 已贴近该子集的结构上限。
3. **MAPE 失效**：~2Hz 平滑对齐数据含非切削段，76 个窗口均力 <1N、全体 min=-104.5N（力符号翻转）——分母近零导致 MAPE 爆炸。后续对比一律以 R²/MAE 为准。
4. **v1 的 0.998 是合成数据的构造性结果**：合成网格中力由参数解析生成，真实数据中力还受磨损阶段、刀具状态、采样平滑影响——这个落差是"合成→真实"的诚实代价，不是回归退步。

## 4. 物理合理性抽查（I2 §2.2 要求）

- nuaa 内 tag 级：力-进给 Pearson +0.166（方向正确，弱）；力-切深 -0.291（被刀具寿命长度混淆——寿命长的 tag 磨损段占比高，抬高了窗口均力）。
- 窗口级：力随进给/切深的单调性在窗口均力层面不成立（原因同上），已如实记录进 `build_summary.json`。
- **人工抽检**：`data/training_sets/cutting_force_v2/manual_audit_20.csv`（seed 42 随机 20 窗，含量纲/工况一致性核对列）——**待用户人工核对并签注**，这是 I2 验收清单最后一项的人工环节。

## 5. 产物清单

| 产物 | 路径 |
|---|---|
| 注册表权重 + manifest（口径 B 训练，含达标结论） | `engineering/python/models/lnn/cutting_force_v2.npz` + `.manifest.json` |
| 口径 A 留档产物 | `engineering/python/data/training_sets/cutting_force_v2/eval_random/` |
| 数据集 + 工况表 + summary | 同目录 `uniwear_force_v2.csv` / `conditions_lookup.csv` / `build_summary.json` |
| 人工抽检清单（待签注） | 同目录 `manual_audit_20.csv` |
| 训练脚本（新增 --split-col 双口径支持） | `engineering/python/scripts/train_registry_model.py` |
| 注册表条目 | `app/ai/lnn/inference/_lnn_registry.py` → cutting_force_v2（CFC，4 特征契约） |

回归：新增单测 13 passed；注册表相关回归 101 passed（test_lnn_inference + test_lnn_weight_persistence）；ruff 全绿。

## 6. 后续选项（需拍板，均不影响当前诚实交付）

1. **接受现状（推荐）**：v2 定位为"真实数据诚实基线"，D7 叙事 = 0.998（合成）→ 0.542（真实，上限 0.41 结构解释），答辩讲信息论上限比硬凑 0.90 更硬。
2. **数据清洗迭代**：剔除非切削段（窗口均力 <0 或 <1N 的窗口），重训看 R² 变化——改善有限（结构上限不变），成本低，可做。
3. **目标重定义（改服务契约）**：预测"力残差（相对 tag 均值）"需要磨损/时间信息入特征——推理时不可得，破坏服务契约，仅作科研对比不入 registry（同 I2 §5 v2b 边界）。
