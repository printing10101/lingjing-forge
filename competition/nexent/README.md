# Nexent 技能提交材料

当前包含四个基于现有 MCP 工具的技能：

| 技能 | 工具 | 能力与范围 |
|---|---|---|
| manufacturing-evidence-review | dxf_describe_file、cam_recommend_process、cam_get_quadruple_stats | 整理图纸摘要和工艺推荐证据；不构建本体 |
| hole-process-draft | cam_recommend_process、process_plan_run | 从明确的结构化孔输入生成方案/NC 草案；不完成物理校验或下发 |
| manufacturing-failure-review | gcode_get_failure_stats、gcode_list_failure_cases、cam_get_quadruple_stats | 案例复盘和改进提案；不写知识或宣称已自进化 |
| manufacturing-knowledge-update | cam_submit_knowledge_candidate、cam_get_knowledge_candidate、cam_recommend_process | 提交待审资料、回读人工审核结果、再次查询正式推荐；不批准知识 |

`skills/` 是可编辑来源，`dist/` 每个 ZIP 根目录直接包含 SKILL.md，可分别上传至 Nexent 的“我的 Skill”。
上传后须选择与源文件相同的实际 MCP 工具并配置权限，再在智能体中绑定技能。
2026-10-16 在线验收：三个 Skill 已上传并绑定到 `process_advisor`（agent_id=1），工具映射和启用状态已回读确认。
六个 MCP 工具已通过 Nexent 的实际工具校验链路调用桌面后端。
尚未完成大模型自主选择和执行 Skill 的对话验收；当前有三条待审候选，有效批准数为 0，推荐接口连通不代表推荐内容有效。
恢复后的两个文档知识库可读取三个文件和 25 个分块；I10 已通过基础向量检索及独立私有验收库内的新 Markdown 文件处理、入库、来源检索。
工艺知识持久化、写盘失败撤回、材料匹配和演示配方隔离均已修复；尚无有效批准，不能宣称真实工艺推荐已通过。
完整证据和限制见 `docs/paper_and_competition/I10-工艺知识持久化与向量检索验收-2026-10-16.md`。

I11 新增 `knowledge-candidates/`：厂商 PDF 选取事实、出处、校验值和待审核候选，
并在独立 PRIVATE 知识库完成入库和可追溯检索。没有自动批准或绑定到业务智能体。
来源审计及本轮限制见 `docs/paper_and_competition/I11-厂商来源抓取与候选知识验收-2026-10-16.md`。

I12 已修复材料 ID 关联、刀具范围中点误用及刀具材质参数匹配，草案显示候选规格和未核验来源。
参见 `docs/paper_and_competition/I12-材料关联与刀具规格整改验收-2026-10-16.md`；这不代表实际刀具或正式工艺验收通过。

I13 新增工艺知识审核与版本 API：待审、批准、拒绝、撤销和修订可追溯，正式推荐不再按来源标签放行。
真实厂商候选仍待审核；审核界面和完整 Nexent 知识更新联运尚未验收。
参见 `docs/paper_and_competition/I13-工艺知识审核与版本验收-2026-10-16.md`。

I14 已交付工艺知识审核页面，完成隔离合成资料的浏览器操作与真实 HTTP 生命周期验收。
待审来源和未解决事项可以持久保存；真实厂商资料及完整 Nexent 知识更新联运仍待验收。
参见 `docs/paper_and_competition/I14-工艺知识审核界面与交互验收-2026-10-16.md`。

I15（2026-10-17）新增 manufacturing-knowledge-update，已导入并绑定到现有 process_advisor 草稿。
新增 MCP 工具 cam_submit_knowledge_candidate 和 cam_get_knowledge_candidate，提供待审提交与状态回读。
三条 Dormer 厂商资料已通过 Nexent 实际工具链进入待审队列，重复提交不新增、有效批准数为 0。
隔离合成测试已完成“提交 → 人工批准 → 再次推荐 → 撤销”；真实知识的批准及大模型自主执行仍未验收。
参见 `docs/paper_and_competition/I15-Nexent候选知识更新闭环验收-2026-10-17.md`。

I16 补充了三条厂商候选的材料分组、进给选表规则、R10A规格和目录号，生成可重建的人工审核资料包。
速度单位仍未知，原未解决事项保留，没有修改线上候选或批准记录。
新增12项真实闭环验收步骤，并明确推荐接口尚未自动核验硬度、孔深、刀具及冷却条件。
本轮线上只读检查未成功，不能把I15状态当作本轮实时复验结果。
参见 `docs/paper_and_competition/I16-厂商适用条件补充与审核准备验收-2026-10-17.md`。

建议先跑以下例子，保存实际调用记录与结果：

1. 提供白名单内的 DXF、明确材料 ID 和加工特征，要求整理证据；检查缺少深度时不补造尺寸。
2. 提供完整材料与孔列表，要求生成草案；检查阶段警告原样可见且没有上机承诺。
3. 要求复盘最近失败；检查引用实际案例并将修改建议与已执行更新分开。

这些技能可以由同一智能体按任务阶段选择，当前没有新增“技能调用技能”的工具或验证嵌套执行。
赛题中的知识构建、完整检索推理双驱动、动态本体和校验反馈写入仍需进一步实现与联运。
打包与本地 Nexent SDK 解析记录见 `docs/paper_and_competition/I8-validation/skills-package.json`。
