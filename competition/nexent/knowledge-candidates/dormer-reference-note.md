# Dormer Pramet 钻削来源候选（待审核）

标记：DORMER_R10A_REFERENCE_PENDING_REVIEW

本资料仅供来源检索，不是已审核的工艺推荐，不允许直接生成上机参数。

原始文件：https://api.dormerpramet.com/medias/dormerpramet-ptd-nas-drills.pdf?attachment=true&context=bWFzdGVyfHJvb3R8NjY5NTk3MHxhcHBsaWNhdGlvbi9wZGZ8YUdaaEwyaG1ZeTg0T0RNeU56azFNVFE0TXpFNEwyUnZjbTFsY25CeVlXMWxkRjl3ZEdSZmJtRnpYMlJ5YVd4c2N5NXdaR1l8YzU0NzRiMTIzNTVjNjk3NTE3OWQ2ZjY4Mzc0YTc5ZTY2ZDEyZDk2YWRiNzdlNjBkM2YyMDQ1MzQwYzU2MmZkNQ

PDF SHA256：c749bdee685f53038b94b5b116d004155623af651e1068f1e41041bb4b867fe7

原文定位：PDF 第 4 页进给表与材料组，第 5 页 R10A 工具列。

| 材料组 | 条件 | 原始代码（速度单位待确认） | 原始进给 in/rev | 换算进给 mm/rev |
|---|---|---|---|---|
| dormer_amg_1_3 | <24 HRC；直径 7.9375 mm | 89G | 0.0063 | 0.16002 |
| dormer_amg_1_4 | <24 HRC；直径 7.9375 mm | 79F | 0.0054 | 0.13716 |
| dormer_amg_2_2 | <24 HRC；直径 7.9375 mm | 36G | 0.0063 | 0.16002 |

工具限定：Dormer Pramet R10A HSS；原表进给允许偏差 ±25%。

未决项：速度单位、具体材料牌号与厂商材料组的等价关系、冷却/装夹/孔深适用性及工艺人员审核。没有计算或确认主轴转速。

版本审计：仓库现有切削参数共 609 条，12 条标记 repo_seed_v1，597 条标记 handbook_common_range；缺少可核验外部出处，不能用本次 3 条候选为全部条目背书。旧在线说明的 12 条为历史快照。

检索到本文件时必须保留待审核标记和适用条件。正式四元组推荐不包含 manufacturer_candidate 来源。
