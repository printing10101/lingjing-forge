---
name: hole-process-draft
description: 根据已明确的材料与结构化孔参数生成孔加工工序和 NC 草案，并核对生成阶段的错误与默认参数；适用于孔加工方案准备。
allowed-tools:
  - cam_recommend_process
  - process_plan_run
---

仅处理已有结构化孔描述的工艺草案。调用之前核对材料、孔位置/直径/深度、坐标与单位、
控制器、安全高度和程序号是否已经明确。DXF 摘要中的圆数量不构成完整孔描述。
缺少影响刀路的输入时列出缺项，不能虚构孔深、厚度或材料牌号。

必要时用 `cam_recommend_process(feature="hole", material=已确认的材料ID, top_k=3, conditions=已确认的现场条件)` 核对知识覆盖。
conditions 需要 tool、hardness_hrc、diameter_mm、depth_mm、coolant、workholding。冷却选项为 dry/flood/mist/through_tool，装夹为 vise/chuck/fixture/collet。
conditions_missing 时询问缺失信息并停止正式参数推荐；conditions_mismatch 时说明不符项；unsupported_process 时明确暂不支持自动适用性核验。
不得推测硬度、刀具、冷却或装夹来凑齐条件；matched 也不能代替物理校验。
输入明确且用户请求生成方案时，调用 `process_plan_run`：

- part_description 包含 material 与 holes 列表；holes 每项采用实际工具 schema 和已有描述中的字段。
- controller_type 只能为 fanuc_0i、siemens_840d、heidenhain_tnc、xmachine_xm100。
- holes 最多 50 个，描述不超过 64KB；超限时报告，不静默截断零件。
- safe_z 单位为 mm，范围 1–500；program_number 范围 1–9999。

当前网关没有显式 stock_top_z 和 max_stepdown 参数，不能声称它们已被传入。
返回值应保留各阶段状态、错误、警告、工序、切参和程序文本；阶段失败或安全警告不能被外层成功包装掩盖。
使用默认参数、知识匹配失败或控制器适配限制时须随结果报告。

输出为待校验草案。生成成功不等于三维度校验完成，也不等于可上机。
本技能的工具集不含完整评估、审核或 DNC 下发，不得声称这些步骤已执行。
若用户要求可用性结论，交接到软件的正式 CAM 校验流程，并记录实际报告与对应 NC 内容指纹。
仅凭通过率、复合分或其他文件的报告不能对本程序放行。
