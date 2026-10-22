# prototypes/ — 原型状态

更新：2026-10-21。

| 目录 | 当前定位 |
|---|---|
| `pointcloud_registration/` | 当前探索：叶片点云配准、PCA/ICP 与对称性诊断；尚未形成论文贡献结论 |
| `surface_edit_probe/` | 保留早期解析探针、独立真值测试与原始扫描结果 |
| `surface_edit_pilot/` | 保留几何求解与 STEP 内核；challenge/dialogue 仍依赖它；两批结果各有用途 |
| `surface_edit_challenge/` | 保留 10 几何/30 需求、确定性强基线和失败分类 |
| `surface_edit_dialogue/` | 多轮方法论文路线已停止，保留正式/校准模型响应、历史对照与负结果 |
| `tool_conditioned_surface_gate/` | 宽泛刀具条件路线不立项；保留经典几何探针和查新留档 |
| `shared/` | 共享契约、注册表和评估；包初始化仍引用，保留 |

已删除的独立旧入口：`agents_research/`、`lnn_research/`（Bayesian 零值推理占位、导入已失效的 Bayesian/Cross-Layer 原型）。全仓运行引用核查未发现消费者；工程侧同名禁用枚举保留兼容，不表示实现仍可用。

关闭依据和保留边界见 [清理记录](../../docs/reports/research_cleanup_2026-10-21.md)。冻结结果、来源哈希和原始模型响应不是缓存。
