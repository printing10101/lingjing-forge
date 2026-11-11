# prototypes/ — 原型状态

更新：2026-11-11（新增薄壁探索台账，其余条目沿用既有记录）。

| 目录 | 当前定位 |
|---|---|
| `thinwall_inverse_design/` | 最终壁厚/加强筋方向的阶段收束记录；实际实现梁条与分片板、切削稳定性及来源诊断，尚未实现筋板或证明新设计优势；第五轮完成 Micromachines 2022 锚点验收（判不满足完整可复核，Fig.8 数字化得 13 个带标签点）；当前版本、撤回项、未决项见[F17台账](../../docs/paper_and_competition/F17-薄壁最终结构逆设计探索台账-2026-11-11.md) |
| `contact_repair_evidence/` | 受限刀球修正探索：实际几何 minimax、精确锁定与证据回放、协同节点诊断；F15/F16 留档；新选择规则尚未超过强基线，未确立专利贡献 |
| `pointcloud_registration/` | 当前探索：叶片点云配准、PCA/ICP 与对称性诊断；尚未形成论文贡献结论 |
| `surface_edit_probe/` | 保留早期解析探针、独立真值测试与原始扫描结果 |
| `surface_edit_pilot/` | 保留几何求解与 STEP 内核；challenge/dialogue 仍依赖它；两批结果各有用途 |
| `surface_edit_challenge/` | 保留 10 几何/30 需求、确定性强基线和失败分类 |
| `surface_edit_dialogue/` | 多轮方法论文路线已停止，保留正式/校准模型响应、历史对照与负结果 |
| `tool_conditioned_surface_gate/` | 宽泛刀具条件路线不立项；保留经典几何探针和查新留档 |
| `shared/` | 共享契约、注册表和评估；包初始化仍引用，保留 |

已删除的独立旧入口：`agents_research/`、`lnn_research/`（Bayesian 零值推理占位、导入已失效的 Bayesian/Cross-Layer 原型）。全仓运行引用核查未发现消费者；工程侧同名禁用枚举保留兼容，不表示实现仍可用。

关闭依据和保留边界见 [清理记录](../../docs/reports/research_cleanup_2026-10-21.md)。冻结结果、来源哈希和原始模型响应不是缓存。
