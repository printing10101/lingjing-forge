# Nexent 技能提交材料

当前包含四个基于现有 MCP 工具的技能：

| 技能 | 工具 | 能力与范围 |
|---|---|---|
| manufacturing-evidence-review | dxf_describe_file、cam_recommend_process、cam_get_quadruple_stats | 整理图纸摘要和工艺推荐证据；不构建本体 |
| hole-process-draft | cam_recommend_process、process_plan_run | 从明确的结构化孔输入生成方案/NC 草案；不完成物理校验或下发 |
| manufacturing-failure-review | gcode_get_failure_stats、gcode_list_failure_cases、cam_get_quadruple_stats | 案例复盘和改进提案；不写知识或宣称已自进化 |
| manufacturing-knowledge-update | cam_submit_knowledge_candidate、cam_get_knowledge_candidate、cam_recommend_process | 提交待审资料、回读人工审核结果、再次查询正式推荐；不批准知识 |

`skills/` 是可编辑来源，`dist/` 下的最终包为 `*-i18.zip`：每个 ZIP 内层路径为 `<skill-name>/SKILL.md`，新建和更新均使用该结构。
上传后须选择与源文件相同的实际 MCP 工具并配置权限，再在智能体中绑定技能。
`*-i17.zip` 与无版本后缀的旧包只保留在本地历史中，不应再用于线上更新。
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
## I20 本体扩展提案（2026-11-10）

补上赛题任务要求②中"本体支持概念、关系、属性扩展"这一环。此前只有固定本体
（Material / Tool / Feature / Process / FailureMode / Controller + 5 类关系），
新概念无法进入本体，只能改代码。

新增三个 MCP 工具（`mcp_server/ontology_tools.py`）：

| 工具 | 权限 | 作用 |
|---|---|---|
| `cam_propose_ontology_extension` | B（写待审队列） | 扫描一份知识候选，检出固定本体之外的类型/关系/属性键，登记为待审提案 |
| `cam_list_ontology_proposals` | R | 列出提案，默认只返回 pending |
| `cam_get_ontology_registry` | R | 列出**已生效**的类型，仅含已人工批准项 |

**不提供审批工具**：批准是知识治理动作，只能由人经
`python -m mcp_server.ontology_review approve <proposal_id> --reviewer <name>`
或审核界面完成。智能体可以把提案摆到人面前，不能替人拍板——与
`cam_submit_knowledge_candidate` 同一条设计原则。

真机检出结果（3 条 Dormer 候选，证据 `ontology-evidence/pilot-scan-2026-11-10.json`）：
检出 4 个未登记属性键 —— `feed_per_revolution_mm`、`feed_min_mm_per_rev`、
`feed_max_mm_per_rev`、`hardness`（规范写法为 `hardness_hrc`）。扫描后注册表不变，
4 条全部 pending。其中三个 `feed_*` 属单位后缀别名，预期人工拒绝；`hardness` 属
规范键名不一致，预期批准并把同义词表补进 schema —— 这正是"必须有人工审核"的例证，
不是噪声缺陷。

验证：
- `python -m unittest discover -s app/knowledge_graph/tests -p "test_ontology_proposal.py"` → 7 passed
- `python -m unittest mcp_server.tests.test_ontology_tools` → 6 passed

包级改动：`app/knowledge_graph/__init__.py` 改为惰性导出（PEP 562），使
`ontology_proposal` 的零依赖特性不被 networkx / SQLAlchemy 破坏；`from app.knowledge_graph
import KnowledgeGraphQueryAPI` 用法不变。

平台侧还需一步人工动作：重启 MCP 服务后在 Nexent 中刷新工具列表，确认三个新工具
可见并配置权限；当前只完成代码与本地验证，尚未在 Nexent 内实调。

## I21 行业标准对齐（2026-11-10）

补上赛题任务要求②后半段"行业规范对齐"。目录 `standards/`：

- `standards.json`：4 项标准元数据（GB/T 12204-2010、GB 15760-2025、GB 15760-2004
  〔已废止〕、GB/T 1031-2009），只取全国标准信息公共服务平台的公开信息，全文未获取。
- `rule_map.json`：6 条内部规则 → 标准的映射，强度分 direct / none。
- `standard_alignment.py`：日期感知的有效性判定、废止引用阻断、条款级缺失显式化、
  合规表述检测。零第三方依赖。
- `alignment-audit-2026-11-10.json`：审计结果。

审计计数：total 6 / usable 3 / blocked 1 / gaps 2 / clause_level_unverified 4。
单测 7 项通过。

两个刻意的设计：

1. **有效性按查询日期判定**，不是写死状态。GB 15760-2025 实施日 2026-08-01，
   在 2026-07-01 查询应为"尚未实施"。赛季内正好发生这次换版，这条能被评委当场验证。
2. **无国标的规则记为缺口**。颤振切深比判据、K001–K009 校验编号没有直接对应国标，
   记为 gap 而不是硬凑——K 码是本项目自定义编号，若包装成国标条款就是造假。

## I22 自主编排验收协议（阻塞中）

`acceptance/` 下两份：

- `agent-orchestration-acceptance.md`：三组对话用例（阶段切换 / 越权诱饵 / 三源多跳）
  加判据表，可照抄执行。
- `check_env.py`：前置只读检查。

**当前阻塞（已定位）**：不是用户加的黑名单，是代理沙箱的内置命令预设拦了 wsl.exe。
证据：Docker 后端日志 `fork/exec C://Program Files\WSL\wsl.exe: Access is denied`；
`~/.workbuddy/settings.json` 里 `sandbox.orderedRules.command` 为 `customized:false, rules:[]`；
而 WSL 本身正常（注册表有 `docker-desktop` 发行版，`wslservice` 在跑）。
成因：Docker 由沙箱内启动时，`com.docker.backend` 继承限制，执行 `wsl --version` 被拒。

解法：在桌面手动双击 Docker Desktop（沙箱外启动），或移除 wsl.exe 黑名单后重跑
`check_env.py`。该脚本已内置日志根因诊断。

未验收就不写"已验收"：第 9.2 节第 3 条维持"模型自主编排未验收"。

## I23 Skill 契约校验与新技能（2026-11-10）

契约校验暴露一个真实遗漏：本体提案的 3 个工具没有任何 Skill 绑定——工具在服务端存在，
但智能体按 Skill 编排时调不到。补第 5 个技能：

- `skills/manufacturing-ontology-maintain/SKILL.md`（打包 `dist/manufacturing-ontology-maintain-i18.zip`）
- 绑 3 个本体工具；边界写明：不批准提案、不写工艺参数、不把别名当缺口上报

`acceptance/skills_contract_check.py` 查四件事：工具存在性、审批/下发类工具禁令、
边界声明、未覆盖工具是否登记理由。当前结果：

- 服务端 20 个工具，5 个 Skill 覆盖 11 个
- 9 个有意排除，逐条写明理由（LNN 训练类、gcode job 写类）
- 错误 0、警告 0

关键：校验器带 `--self-test`。注入一个坏 Skill（工具名不存在、绑定审批工具、无边界声明、
描述过短），确认 5 类缺陷全部命中。没有这个反向自检，"零错误"可能只是"什么都没检查"。

仍未验收：模型在 Nexent 里自主选 Skill。Docker 引擎仍因 wsl.exe 被安全策略阻止而起不来，
`check_env.py` 依旧 2 项 BLOCK。契约层已自洽，模型层待环境放行。

## I24 自主编排实测与收束（2026-11-10 22:25）

环境全部打通（后端 8765 / MCP 8088 / 模型 8080 / 转发 9080 / 向量 8090），Nexent 侧
62 工具、5 Skill 全绑定，随后跑了用例 A 两轮：

| 轮次 | 耗时 | 工具调用 | 结果 |
|---|---|---|---|
| 第一次 | 1193 s | 14 次（含 process_plan_run） | 答案 0 字符，finish_reason=length |
| 第二次（token 上限提到 8192 后） | 1080 s | 11 次 | 同上 + 3 次 Context size exceeded |

**判定：部分验证。** 行为证据成立——模型两次主动 `read_skill_md` 再决定调哪个工具，
单轮覆盖证据整理、草案生成、失败复盘三类，是自主编排而非人指定。结果质量不成立——
本地 27B 在 16 GB 显存下被 16k 上下文与 4096 输出上限卡死。

两个待修的配置点已定位：模型 `max_output_tokens` 改了但运行时仍按 4096 截断（需重启
`nexent-runtime` 才可能生效）；llama-server 要开 32k 上下文，27B 权重下大概率 OOM。

B 用例 20 分 53 秒未完成，中止；C 未开始。8B 模型下载实测 100 KB/s（走代理）/ 26 KB/s
（绕过代理），当天不具备可操作性。

服务已于 22:25 全部停止，五个端口释放，显存回到 2.1 GB。复原命令见
`acceptance/runs/2026-11-10/README.md`。
