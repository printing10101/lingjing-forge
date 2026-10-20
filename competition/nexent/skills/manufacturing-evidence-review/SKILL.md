---
name: manufacturing-evidence-review
description: 为加工咨询整理 DXF 图纸摘要与已有工艺推荐证据，标明来源、缺失输入和适用边界；适用于规划之前的资料核对。
allowed-tools:
  - dxf_describe_file
  - cam_recommend_process
  - cam_get_quadruple_stats
---

将用户提供的图纸路径、明确的材料和加工特征整理为可复核的决策输入。

有 DXF 路径时调用 `dxf_describe_file(path)`，记录实体统计、图形范围和警告。
该工具只返回摘要，不能由圆的数量推断通孔深度、由包围盒推断板厚，或声称完成三维重建。
路径必须在后端允许的输入目录内；被拒绝时说明需要可访问的路径，不改白名单。

材料和特征已明确时调用 `cam_recommend_process(feature, material, top_k=3, conditions=已确认的现场条件)`。
conditions 包括 tool、hardness_hrc、diameter_mm、depth_mm、coolant、workholding；冷却为 dry/flood/mist/through_tool，装夹为 vise/chuck/fixture/collet。
缺少条件时保留 conditions_missing 并询问用户，不补造；条件不符或加工方式暂不支持核验时，不输出正式参数。
标识使用工具支持的 ASCII ID；只使用用户明确指定或实际资料提供的映射，不把“合金”默认成铝合金。
缺少材料牌号时先列出缺项，不用工具默认 general 的结果代替牌号匹配。
只有用户询问知识覆盖范围时才调用 `cam_get_quadruple_stats()`。

保留每次实际工具调用的名称、输入、输出中的来源/置信度/参数和警告。
返回“已知输入—证据支持的建议—尚缺的条件”三部分；没有返回页码、文件版本或条款时明确缺失，不能补造引用。
推荐为空时说明知识不足，不能声称已获得匹配证据。generated_validated 表示软件生成校验案例，不代表机床实测。

本技能提供证据整理，不执行本体构建、知识库写入、NC 生成、仿真或机床下发。
需要孔加工方案时由智能体另行选择 hole-process-draft；不能把此文档中的技能名称当作可调用工具。
