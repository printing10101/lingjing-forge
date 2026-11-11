---
name: manufacturing-ontology-maintain
description: 从带来源的工艺候选里检出固定本体之外的概念、关系与属性键，登记为待审提案并回读注册表状态；适用于知识口径扩张与键名不一致的排查。
allowed-tools:
  - cam_propose_ontology_extension
  - cam_list_ontology_proposals
  - cam_get_ontology_registry
---

处理本体层面的问题：新材料组、新工序维度、新参数键（例如候选里出现 `hardness`
而规范键是 `hardness_hrc`）。这类问题的特征是——候选事实本身没问题，但**字典里没有
这个词**，于是条件过滤会静默失配。

调用顺序固定：

1. `cam_get_ontology_registry()` 先看现有字典。已有对应类型或键时，不提议，直接说明
   应当用哪个既有键。
2. 确有缺口时调用 `cam_propose_ontology_extension(candidate, submission_id)`。
   `candidate` 传入候选内容，`submission_id` 使用候选的稳定标识，不得自造。
3. `cam_list_ontology_proposals()` 回读提案队列，把待审项连同出现次数呈现给用户。

三条硬边界：

- **不批准任何提案**。工具集里没有审批能力，批准只能由人在审核入口
  （`python -m mcp_server.ontology_review approve ...`）或界面完成。
  不得声称"已加入本体"、"已生效"、"已更新词典"。
- **不写工艺参数**。本技能只处理类型与键，不产出、不修改任何切削参数或知识内容。
  参数相关结论交回证据整理或知识更新技能。
- **不把别名当缺口上报**。同一概念的单位后缀变体（如 `feed_min_mm_per_rev` 与既有
  进给键）应提示为"疑似别名，建议拒绝"，不得直接建议批准。

输出三段：现有字典中已有什么、本次检出的缺口是什么（含来源候选与出现次数）、
需要人做的决定是什么（批准 / 拒绝 / 改键名）。没有证据支撑的缺口不提议案。

本技能不构建本体结构、不做知识图谱推理、不触发重训练，也不改变任何检索行为；
词典更新后是否影响既有推荐，需要另行复验，不得推定已改善。
