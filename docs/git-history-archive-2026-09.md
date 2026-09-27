# Git 历史归档（2026-09-27）

> 原 main 分支共 363 个提交（89c74406 初始化 → 8e9b15e8）。
> 因原仓库为 partial clone（历史 blob 从未完整落盘）且原 GitHub 远程仓被删除，
> 完整历史无法推送到新仓库。本文件保留全部提交标题作为演进叙事记录；
> 本地分支 archive/hollow-history-2026-09 仍保留旧引用，若原远程仓在
> 删除后 90 天内被恢复，仍可据此外科手术式补齐历史。

8e9b15e8 chore: 补 torch/numpy 跨环境配对口径与 Mimosa 门禁调参教训
0e88b45f ci: PR 汇总新增 API 文档同步门禁 job
cbbeefb1 feat(dnc): 逃生阀放行必须写入哈希链审计，审计失败不放行（fail-closed）
edf2af75 chore(security): 剥离 real_validation 会话脚本中的本机用户路径
1598d26c test(tests): 全量重跑后同步压力测试生成物（17/18，FAIL 项为满负荷资源阈值）
521fcab9 docs: 修正与实测/门禁不一致的口径，并记录压力测试的真实结果
766b3062 chore(wip): 入库上一轮会话在飞改动，避免工作树单副本
3426c6f3 feat(security,benchmark): 入库仅存在于工作树的 XML 防护与 S6 可检性闸门
e5413501 fix(scheduler,test,tauri,rust,ci): 修周任务静默不调度、测试编码缺陷、诊断端口硬编码与 Rust 构建链路
89d995e5 chore(git): 清理被跟踪的运行时产物与游离资产，归档到 docs/archive
4d4de6b2 test(tests): 压力测试报告重跑（Python 3.14.4，全项 PASS）
22fca46f feat(research): PHM2010 真实信号快速重训结果
4ab9991b docs: 全仓结构审查报告（2026-09-18）
f24a5757 docs(research): 复杂曲面生成论文线 F0-F6 全套文档
0505b3f2 feat(python): machinability_pilot 曲面 CAD 加工物理评估基准
29c22bf2 chore(git): pilot 真值产物与根目录草稿 CAD 不入库
c1475d66 fix(ci): test-summary needs 补列 python-torch-integrity
6536dda9 test(tests): 绝对延迟断言统一走 CI 感知阈值助手
e9d8e9bc test(tests): 篡改签名测试改为篡改 payload 段
25720949 fix(ci): integration 覆盖率检查注释含裸双引号截断 python -c 脚本
dad3f730 fix(ci): integration 单套件覆盖率门禁改信息展示，perf 回归检测容错解析
682fca52 fix(ci): 前端 test:ci 增加 default reporter
03442614 fix(core): TokenBanList 兜底键改用整 token sha256
28f14c51 docs: I1 数据与模型技术路线——切参扩容/案例库灌数/真实数据重训/三级进化实验落地设计
0e46870b test(tests): 全管道延迟门禁按 CI 环境放宽 3 倍——CI 串行实测 845ms，本机独占保持 500ms 严格口径
2413ed28 fix(python): wear_prediction manifest 数据血缘修复——manifest 相对路径与全长 sha256（C1 收尾）
beed81bf test(tests): JWT 有效 token 断言附 401 报文与密钥指纹，定位 CI-only 解码失败
5ceb3224 fix(ci): 回归 job 关闭全局覆盖率门禁——仅跑 tests/regression 达不到为全量套件设计的 24%
87851093 fix(core): 路径校验捕获 NUL 字节 ValueError——Linux 下拒绝而非抛 500
66f4d402 docs: 发版报告桌面构建专项关闭——run 35280667180 三平台全绿
fff3a7a1 ci(desktop): Linux 打包目标改为 deb，放弃 AppImage
ff95a118 ci(desktop): 桌面运行时裁剪 tkinter/tcl-tk，修 AppImage linuxdeploy 依赖解析失败
33205a61 ci(desktop): uv 缓存限非 Windows；tauri build 加 verbose 取 linuxdeploy 真实报错
1640b1ef test(ai): validate_model_file_exists 显式传权重路径，消除 CWD 依赖
513f0858 ci(desktop): 修 pip --target 不认已装包——CPU torch 改由索引优先级保证
b66cc8d6 fix(research): 修引擎数值债——LTC 求解降级锁存、物理分支域钳制、梯度裁剪
8d7d3e1f ci(desktop): 修 Linux 打包失败并消除 2 小时 CUDA 依赖下载
e678fe79 docs: MCP 工具面文档化——26 工具清单、鉴权模型与 5 分钟接入示例
f6adc671 docs: 文档站元信息同步新定位桌面级制造物理 AI
5490a1e6 docs: 新叙事定位落地四处入口文档，核正 kinematics 口径，发版清单加对照表检查
3ae1036a test(simulation): kinematics 校验器补齐 K001-K009 单元测试（17 用例）
4152e83d fix(tests): 飞轮 E2E 隔离单例数据湖，清除被测试记录占满的入库训练数据
34f51bbc chore: 源码提示文案去 emoji 并中性化外部工具品牌表述
40357359 docs: 全仓文档去 emoji 与第三方品牌表述中性化
b4a36dac feat(research): 物理模型失效预警器（Step 4）——LOOCV 与置换检验
1340861b feat(research): Insperger 2003 实测稳定点数字化与论文模型审计
f57f6db6 test(frontend): http 模块加载测试超时 15s 放宽至 30s
d6a1a564 feat(frontend): 路由切换同步浏览器标签页标题
b6aa7022 fix(frontend): keep-alive 全量缓存改为白名单
e2adb919 feat(frontend): 头部假搜索框升级为全局快速搜索，导航数据源 i18n 化
a50a6a36 docs(frontend): 重构方案标记落地结果与偏差说明
d5d6f197 refactor(frontend): 组件归一与 i18n 瘦身——图表路径统一、目录改名、死 key 清理
4a0e5e77 fix(frontend): 半成品页面治理——Goals/设备/物料/建模→仿真链路
16d28fdf refactor(frontend): 页面合并——37路由收敛至24，消除同源页面冗余
3cef54a6 refactor(frontend): 清除死代码与死依赖——UXDemo/命令面板/examples/4个无引用store
8e59165c fix(research): 实测稳定性数据集 NaN 治理与稳定性判定方向修复
7ce0bd5d feat(process_planning): 刀具直径三级来源贯通——工艺规划 → G代码 → CAM 体素校验
3f0d2784 feat(cad): NL2CAD 参数化直调滑杆——脚本参数免 LLM 重执行 + 视觉回看校验
3961a95b fix(agents): Ingress 鉴权兼容无 Bearer 前缀的 Authorization 头
1f42d24c fix(agents): 远程 SSE 模式放行容器网关主机名（Nexent 集成）
cdb666af style(python): ruff format 对齐 CI 版本（0.15.12）
17b68e9a test(tests): knowledge_retrieval 加共享 chroma_db 并发竞态守卫
bf6b99f7 docs(docs): A6 重出 v2.8.0 发布就绪报告（真实数字口径）
11206ff5 docs: A7 文档口径收敛——白盒化任务状态七处矛盾统一为 git 证据口径
85f25ab5 fix(ci): A4 红门禁治理——lint/响应模型/前端测试/并行守卫
37ced72a fix(tests): P0-A 工程基线修复——测试全绿治理与依赖防漂移
cf4b22c0 fix(research): uniwear.csv 入库，修复 wear_prediction 数据血缘（C1）
1a1e6a65 feat(engineering): 前端 RL/世界模型路由与 Agent 仪表盘扩展 + Tauri 命令同步
25df4e0a chore(git): lint-staged 的 cargo fmt 补 manifest 路径
1cab43bb fix(python): 数据与推理链路诚实化加固（bosch/dnc/gateway/校准器/飞轮）
9b323bf4 feat(backend): signal_fusion_kb attention 融合默认拒绝未训练权重（学术诚信守卫）
34252fe1 feat(python): RL agent 权重真实加载与离线轨迹训练环境（学术诚信诚实化）
2e4497f0 feat(python): M2 收尾——AgentRuntime 接入 agent_gateway REST，SHARP/mcp 去留决策落档
8b07e0d9 feat(python): M2 阶段一——统一 AgentRuntime 核（制造工具集 + ReAct 循环 + 轨迹存储）
d55fdf87 docs(python): 自进化路线图 M0/M1 实现记录与 API 文档端点同步
5a539921 feat(python): M1 自进化闭环运转——演化引擎、提示词版本持久化、REST、心跳唤醒、workflow 模板
0563d7a7 feat(python): M0 自进化数据留痕——统一评分服务、失败入册、Prompt Registry、反馈接线
875cffbc fix(cad): NL2CAD 沙箱迁移到可强杀子进程，根治 Linux OCCT 挂死
b60cb0aa docs(readme): 同步文档与代码现状（shared/ 移除、后处理器口径、拆分状态）并补 CHANGELOG
66ad2822 fix(mypy): rules/api.py 修 3 处类型错误并撤销已失去前提的 mypy 排除
dbfc5bed chore(git): 移除 output/ 中误入库的个人求职自荐材料（4 个文件，磁盘保留）
96eccbbb feat(mcp): 端到端工艺规划暴露——决策智能体可从零件描述直达 G 代码
d3dd6511 feat(mcp): DXF 图纸解析端点暴露——CAM 链路最上游感知补齐
b22b59ca feat(mcp): G 代码生成任务 job 化暴露——决策智能体可发起生成并追踪进度
3b09d152 feat(mcp): CAM 主链路只读工具组接入 Agent Gateway——决策智能体调得到 CAM 能力
bbb5ada2 feat(gcode): 种子集扩充至 10 例——门控一轮重放即出可信判定
af6a863e feat(gcode): 灰度重放工具 M5——基线/候选双库重放 + 回归门控操作闭环
8829f099 feat(gcode): 失败案例库 M4——成功案例自动入 RAG 工艺库 + 提示词迭代回归门控
52ca3e82 feat(gcode): 失败案例库 M1-M3（Phase 0 自进化第一性管道）
409be948 feat(ai): 新增 Llama 本地 Provider 并修复本地推理的系统代理劫持
8f4d1541 feat(dxf): DXF 处理链打通真实 G-code 生成与材料透传
38a40dbd fix(ci): Linux AppImage 打包 NO_STRIP+免FUSE 运行；docker job 补 id-token 供 cosign keyless 签名
522161d4 fix(ci): Linux 桌面补 libfuse2 修 AppImage 打包 + Docker 构建超时 30→60 分钟
398248ac fix(ci): 桌面三平台构建阻塞项清障 + Docker 换源 + 测试超时再放宽
9497b8ac fix(tauri): beforeBuildCommand 直用 npm run build（修正 cwd 语义误判）
e0d7c817 docs(ci): CHANGELOG 新增 Unreleased 小节，记录 CI/版本管理修复与 NL2CAD Linux 挂死已知问题
ab535d7c fix(ci): 发版流水线校验 tag 与 VERSION 一致，发布说明改取自 CHANGELOG.md 版本小节
d23b28e2 test(python): 环境守卫与 nl2cad 退化几何用例标注 skip_ci，消除 Linux CI 必挂与挂死
b4dcfe86 fix(ci): 单元测试 job 的 -m not-skip_ci 过滤从未生效且步骤必失败，修复并补 --timeout=300
0f939f44 chore(ci): 统一 Node 22 与 pnpm 9.15.9 定版，package.json 补 packageManager/engines
6953ef7f fix(tauri): beforeBuildCommand/beforeDevCommand 改为跨平台写法
2a258c3c fix(ci): 单元测试与桌面构建超时上限对齐真实耗时
ee72738e fix(test): skill_marketplace 契约断言改用真实返回值
5f629e2d fix(ci): 契约覆盖率阈值 90% -> 65% 校准真实基线
9af89ada fix(ci): 固化前端测试参数为 test:ci 并兼容 istanbul 原始覆盖率格式
b6b7ebe5 chore(git): 忽略会话产物目录并落地 run-dev.bat 的 vswhere 动态定位
ed2ac125 fix(scripts): response_model 基准跨机可移植 + 运行时探测按平台取可执行文件名
cd4cb3a9 fix(ci): 统一 pnpm 9 对齐 lockfile 9.0 并补齐测试依赖
fd3df48a chore(security): 加固 gitignore 并沉淀公开仓安全 instincts
0467075f docs(security): TLS 文档中的 PEM 示例改为占位文本
cb5d87a0 chore(security): 清理仓库中的本机绝对路径
f96dd6ea feat(simulation): 程序级运动学校验 + 平面刀轨引擎 + 工艺特征码增强
9bfa0a4b feat(ai): 注册表模型训练管线与首批随包真实权重
a55e01a0 feat(python): 注册表模型训练集导出适配器与权重桥接
27cb6eb8 fix(db): 数据集版本提交前血缘先落库修复外键必炸
7d02f3fe fix(ai): LNN 权重持久化 v2 npz 与随机初始化显式标记
0ef32ffe docs: LNN 权重训练与分发接线方案（盘点+四工作包+执行记录）
76c67f41 fix(python): torch.load 反序列化 RCE 防护补全
12d6f2ae test(python): LNN NumPy 双副本 parity 锁
73b90782 fix(rag): legacy 向量库自动归档重建——升级路径自愈闭环
58979ffd chore(git): 仓库卫生——补会话产物忽略规则 + 统一 LF 换行策略
6a3925d7 test: 2026-09 存量失败治理——测试隔离、Windows/Starlette 适配与诚实 skip
7a1d2f1e fix(dxf): P1 特征桥——孔/平面明细随 summary 下发，dxf_to_gcode 链规划不再为空
d77642d6 fix(data): P2 数据面加固——材料 schema 适配 + 合成数据入参强转
c71f883e fix(api): P2-11 嵌入检索并发防护——进程级互斥 + 规模上限
89c3e1df fix(agents): P2 编排器与记忆加固——同步 I/O 出事件循环 + 记忆损坏容错
caaa7387 fix(ai): P2 弹性链加固——熔断半开并发闸门 + LLM 错误信息防泄漏
2b89bece fix: LNN 权重断层 P1-4 攻坚——NumPy 推理模型类回迁，4/4 模型在线预测可用
3da78b76 fix: chromadb P1 攻坚——RAG 向量库恢复生产可用（10/10 能力全通）
02ebcbfe fix: 引擎验证第一梯队修复——RAG 引用/工艺理解主接口/路由测试/引导重放
b9bc03d2 fix: AI 引擎全面能力验证——修复 WM 融合默认值与 RL 输入适配两个 P1
87604852 docs: 遗留问题盘点——API 基准在线补数、文档一致性修正
db453a01 fix: world_model 基准维度缺陷修复——性能基准 10/10 全绿收官
1e8025b3 feat(ui): W10.2 Onboarding 制造流程引导——图纸到 NC 全流程 walkthrough
b61d1b83 feat: W9.4 性能白皮书 + W10.1 MCP 扩面确认——按套件容错重跑 9/10 实测
477aab7b feat: 物理AI W系列落地——修复闭环/主权接线/预演卡/dreaming API/RAG溯源
08028e59 ci: post-merge Docker 构建显式使用 Docker Hub 官方基础镜像
b2093bd5 ci: pr/post-merge 的 PYTHON_VERSION 同步升至 3.12
9656d536 ci: 修复 secret 扫描空结果误报与桌面运行时脚本的编码崩溃
59876b5c ci: 修复 pnpm 版本不匹配、uv 安装路径漂移、回归检查空库崩溃与 gitleaks 豁免
c1066c4b chore(cleanup): 忽略 engineering 子目录的本地扫描缓存
fbc10eb7 ci: 修复六类导致流水线长期全红的流水线级缺陷
64949654 test(tests): 收敛顶层孤儿测试并补齐 docs-site 版本同步门禁
d72bf3c4 test: 补齐缺失可选依赖的条件跳过保护并对齐提交期 lint 范围
938e747c ci: api-docs-sync 启用 --fail-on-unsync 升级为真门禁
87aa83d1 docs: 版本元数据同步至 v2.8.0 并收敛变更日志
07f16075 chore(cleanup): 软著个人材料出库；CI 统一 Python 3.12 消除 StrEnum 兼容隐患
068327cc chore(cleanup): 忽略模板 A/B 测试与推理 trace 运行时目录
ab213f6c feat(python): 体素材料去除仿真校验与 DNC 下发硬闸（仿真强制闭环）
12bc819e feat(postprocessor): 方言声明钩子扩展至 tnc640/840d 并新增手动合规验证矩阵
c522228e chore(tests): 覆盖率改为 CI 显式启用，本地测试全速运行
debbc282 chore(cleanup): 移除损坏 venv、运行时日志与大体积数据集的版本跟踪
2821c0a9 chore(cleanup): 同步构建再生的 components.d.ts 与脚本未用导入清理
5e5adc4f fix(scripts): 修复发布后体检发现的失效脚本与存量导入断裂
a4ce1702 chore(cleanup): 注释去日期戳与任务编号叙事，保留技术警示
b2f5c63f chore(config): 发布 v2.8.0
cd4ef2f9 chore(cleanup): 清理僵尸目录与失效脚本，修复 CI 前端检查失效
04beeb01 refactor(frontend): SSE/ECharts/格式化收敛，移除 features 死层与未挂载组件
211e0de7 fix(frontend): 工作台模型列表改走 LNN 模型注册表端点
7d5fddd4 refactor(python): 收敛 Provider/TaskStore/工具函数重复实现
10126494 fix(python): 修复事件循环阻塞、MTConnect 丢数据与错误响应脱敏等隐患
780a595f style(rust): 清理注释横幅并统一rustfmt格式
a6ba21f4 style(frontend): 清理注释横幅与调试日志残留
a04b95e5 style(research): 清理注释痕迹并统一ruff格式
72a74e1b style(python): 清理AI痕迹注释并统一ruff格式
16087424 chore(repo): 大型数据集 CSV 移出版本库，README 注明获取渠道
c38ff966 chore(repo): 仓库卫生清理与功能接线同步（重装前快照）
792ab0b9 feat(api): 数据采集 API (P2-3) 与完整数据飞轮闭环
882c124b feat(python): 数据飞轮 cutting_experience Repository 存储抽象层
29357fcc chore(docs): P2-1 数据飞轮 Schema 设计文档更新为完成状态
eca54b12 refactor(python): dxf 六阶段流水线编排声明化
6ceb61b2 refactor(python): parametric_geometry 两轮审核状态机白盒化
2892efab chore(python): 路由更新与环境适配优化
0c5fd291 chore(docs): 测试环境/API 更新与变更摘要恢复
0d446aa6 feat(frontend): 组件更新与状态管理优化
116f4cff feat(python): 工程稳定性增强完成 - 异常处理/熔断器/中间件体系
81760599 feat(ci): 补全 husky/commitlint/lint-staged 提交门禁
d1bfd0dc chore(docs): 更新测试环境说明与 API 文档，登记 E402 忽略
6eb13327 feat(frontend): 统计卡片与导入组件收尾，扩展 useStatsCards
f3ee1e07 feat(python): 数据飞轮接线、ruff 现代化与关键 bug 修复
80713f19 test(frontend): fix test failures from Phase 1 refactoring
cbd087c4 feat(frontend): 统一统计卡片和导入组件，减少 500+ 行代码
036c4c24 refactor(python): feature_extraction RANSAC 判定逻辑白盒化
e4cc6e1a chore(python): CI 门禁启用 UP 规则固化现代化成果
21ae87a5 refactor(python): 收官批次应用 ruff UP 规则现代化——全 app UP 完成
ecf79352 refactor(python): ai/lnn 应用 ruff UP 规则现代化
0ebe4ebf refactor(python): ai/process_explainer + unified_embedding 应用 ruff UP 规则现代化
6acb8857 refactor(python): plugins 目录应用 ruff UP 规则现代化
bd161357 refactor(python): api 目录应用 ruff UP 规则现代化
e73d26cd refactor(python): services/dreaming 应用 ruff UP 规则现代化
df758191 refactor(python): data/budget 应用 ruff UP 规则现代化
abc236ad refactor(python): tasks/integrations/models 应用 ruff UP 规则现代化
940c3e13 refactor(python): 6 个目录应用 ruff UP 规则现代化 + 修复 2 个真实 bug
8b71d1cb refactor(python): 7 个目录应用 ruff UP 规则现代化
47a1a6d3 refactor(python): 8 个中目录应用 ruff UP 规则现代化
626de9fe refactor(python): 5 个小目录应用 ruff UP 规则现代化
5e988c7a refactor(python): postprocessor 目录应用 ruff UP 规则现代化
6eadca9f refactor(python): dialect 模块应用 ruff UP 规则现代化（试点）
2fcc6f6e chore(frontend): 更新自动生成的组件注册（本轮新增子组件）
bf31ef95 refactor(frontend): 拆分 App.vue 根组件（637→245 行）
bc471e5b refactor(frontend): 删除死代码 useWorkflowSubmit composable（98 行）
bc4ab2c8 refactor(frontend): 删除死代码 useCommandPalette composable（459 行）
c59c46b8 refactor(frontend): 拆分 MaterialManagement 物料表格（474→370 行）
e84e7f95 refactor(frontend): 拆分 QualityInspection 统计卡与记录表格（493→402 行）
7ab3337f refactor(frontend): 拆分 ProcessPlanning 卡片列表（491→335 行）
ee20e7ae chore(frontend): 清理过时的巨型组件拆分 TODO 注释
47d684ba refactor(frontend): 拆分 ApprovalDashboard 重复卡片列表（502→327 行）
32674428 refactor(frontend): 拆分 ProcessUnderstanding 巨型组件（515→246 行）
9c956335 feat(frontend): 设置页接入 settings.tab 扩展点
9d51f4d2 feat(frontend): 命令面板接入 command_palette.command 扩展点
e7341c96 fix(frontend): 修复插件注册顶层 await 导致的生产构建失败
56800812 feat(frontend): 前端插件管道闭环——首个真实插件 dialect-manager
aa2fa60b fix(python): mypy 清理 15 处 + 修复 3 个真实 bug（累计 150+ 处）
ea003b8b fix(python): mypy 清理 18 处 + 修复真实 bug（累计 139 处，473→334）
f9dc7dc5 fix(python): mypy 清理 17 处（累计 121 处，473→352）
e656f6a4 fix(python): mypy 清理 34 处 + 修复过时测试 import 与 2 个真实 bug
790c4a88 fix(python): 修复 3 个运行时 bug + mypy 清理 70 处
bd2ef874 fix(api): 插件系统接线与方言插件市场接入
95ec45a0 feat(api): 方言管理 API 与前端管理页（读路径+写路径+参数）
996f6a6c feat(python): postprocessor 方言声明化引擎与声明镜像
a779bde6 chore(cleanup): 删除 3 个确认死代码模块，补齐 4 处静默异常日志
6ebc8a49 docs(agents): 分支收敛后更新仓库状态（main 唯一分支，refactor 已并入）
ee674e30 chore(test): 清理僵尸测试与调试残留
7b9e40a8 fix(ci): 修复 ruff 门禁漂移与 pre-commit 钩子路径失效
eac979f9 refactor(python): mypy 全量类型修复与注解补全
4e7299e8 chore(git): 隐藏 AI 协作记忆与私密数据（.dsh-memory/.workbuddy/.hermes-memory/.lnn_banned_tokens/.trae/dist_broken）
9d2d437d chore(ci): workflows/scripts/.trae skills 同步；feat: desktop-app 启动脚本与记忆文件入库
8168c15f refactor(engineering): 工程侧代码/前端/测试同步（含 mcp_server 工厂域扩展、golden 测试、SSM 推理等）
88f47567 refactor(research): experiments 物理分组重构（02_LAM/03_PHM2010/04物理感知/05_SSM/05论文图件）与数据集/论文/模型更新
0077abcb docs: 评审输出/论文草稿/变更摘要与调研文档更新
84683f9c chore(git): 全量同步前清理 .gitignore（排除调试探针/缓存/第三方 FEA 二进制）
b047fbaa refactor(process_planning): tool_param_matcher 拆分（_tool_models/_matching/_scoring mixin，波次2）
4b406f57 refactor(postprocessor): heidenhain mixin 拆分（_core/_cycles，波次2）
63ab9a0b refactor(ai): llm router 拆分（_router_models/_latency_cache/_router_core，波次2）
6b152307 refactor(ai): session_extractor 拆分（_session_models/_sources_mixin，波次2）
e44bcce4 refactor(ai): effectiveness_metrics 拆分（_metrics_models/_samples/_compute mixin，波次2）
6b4e71d6 refactor(ai): aligner 拆分（_aligner_models/_losses/_align mixin，波次2）
1b1a0026 refactor(ai): rule_validator 拆分（_validator_models/_checks/_test mixin，波次2）
4df26c93 refactor(ai): task_classifier 拆分（_task_types/_keywords/_rule_classifier，波次2）
1adabba9 refactor(process_planning): plane_recognizer 拆分（_plane_models/_plane_recognize_mixin，波次2）
dc3f7160 refactor(postprocessor): base mixin 拆分（_config/_format，波次2）
26d60813 refactor(db): manufacturing ORM 域分组拆分（_material/_equipment/_quality/_production/_document，波次2）
893152af refactor(dxf): feature_extractor 拆分（_dxf_feature_models/_dimension/_plane mixin/_helpers，波次2）
4ee0b637 refactor(process_planning): boss_recognizer 拆分（_boss_models/_boss_recognize_mixin，波次2）
06e4bc0d refactor(ai): unified_embedding space 拆分（_axes/_space/_holder，波次2）
38c3cab0 refactor(budget): budget 拆分（_budget_models/_tracker/_config/_check mixin，波次2）
3dff4b1e refactor(process_planning): hole_recognizer 拆分（_hole_models/_recognize_mixin，波次2）
a03a9c28 refactor(ai): reflector 拆分（_reflector_models/_dedup/_update/_insights mixin，波次2）
61303d2c refactor(ai): process_understanding engine 拆分（_output/_prompts/_handlers_mixin/_engine_holder，波次2）
db6fe2cb refactor(ai): unified_embedding interfaces 拆分（_enums/_models/_protocols/_flow，波次2）
3aad4917 refactor(ai): inference registry 拆分（_base_registry/_registry_models/_lnn_registry/_runtime_registry/_torch_map，波次2）
84a52d47 refactor(ai): rollback_manager mixin 拆分（_rollback_models/_cooldown/_detect/_execute，波次2）
7c8e3d9f refactor(process_planning): pipeline 拆分（_stages/_stages_mixin，波次2）
1fad2d0e refactor(db): rule_db 拆分（_constants/_version/_models/CRUD/transfer mixin，波次2）
1902e0a4 refactor(tasks): execution 拆分（_session_manager/_task_executor/_engine，波次2）
9c523fab refactor(ai): config_manager mixin 拆分（_schemas/_validation/_persistence/_models，波次2）
1975388e refactor(ai): LNNPredictor mixin 拆分（_batch/_stats/_registry + predictor_types，波次2）
57bf13e5 refactor(process_planning): gcode_generator mixin 拆分（波次2）
3d6c444b refactor(db): training_task 拆分（_base/_rbac_models/_presets/_seed_rbac，波次2）
a0a246b1 refactor(ai): provider_registry 拆分（_db/_cipher/_factory/_registry，波次2）
edcab009 refactor(ai): ProgressivePublisher mixin 拆分（波次2）
a04ba219 refactor(ai): HybridInferenceEngine mixin 拆分（波次2）
df9700cf refactor(rag): routes 服务层抽取（波次2 S4）
7965ec2b refactor(python): postprocessor/config_loader 类文件化拆分（波次2）
447e2c80 refactor(budget): 预算域三件套 mixin 拆分（波次2）
764c358d refactor(api): 4 域任务式路由服务层批量抽取（波次2 S4）
c080d0bb refactor(frontend): Simulation.vue composables 拆分（V1）
0c560cdd refactor(api): gcode_generation 路由服务层抽取（S4b）
f8b1681e refactor(api): cam_validation 路由服务层抽取（S4a）
a3d5128e refactor(ai): ClosedLoop mixin 拆分（S3）
9fc2148b refactor(tasks): TaskCheckoutManager mixin 拆分（S7）
cdbfa4fd refactor(tasks): AsyncTaskManager mixin 拆分（S2）
52013dd6 refactor(python): dxf_parser 实体解析模块化拆分（S1）
6ff81b8c refactor(ai): streaming.py 类文件化拆分（S8）
0030cf88 docs(python): 新增巨型组件拆分方案（P1-4/P1-5 专项调研）
012f6923 feat(api): 关于页检查更新（自动更新过渡方案）
d0af7d3e test(api): 收编 MES/DNC 孤儿测试至标准测试树并适配鉴权
8bb793c3 fix(ui): CSP 增加 unsafe-eval 修复应用白屏
93f05ff1 fix(tests): Python 3.11 兼容 — asyncio.run 替换 get_event_loop + 子进程注入 PYTHONPATH
06f83e4d fix(plugins): contract_adapter 卸载时无事件循环不再静默丢失异步 on_unload
5705fcd1 docs(readme): 更新安装包文件名示例为 ASCII 命名
3592eae3 chore(git): 解除运行时产物跟踪并扩充 .gitignore 防膨胀
a72e85d6 feat(exp52b): 泛化性四场景矩阵（反向跨数据集+分布内跨组，B2 显著正结果 p=0.015）
5626ea09 feat(exp52): 跨数据集零样本迁移实验（审稿人泛化性致命项回应，含诚实负面结果）
cb79f53f fix(test): http utility 全量并行超时放宽 + 新测试类型收口
dee6f162 chore(ci): 门禁基础设施与评分报告（CI 脚本/覆盖率阈值/科研侧解耦）
c0802642 refactor(backend): 科研/工程解耦重构与质量修复
74a55086 refactor(frontend): 前端解耦与测试全绿（vitest 1862/1862 + 生产 bug 修复 + 子组件测试）
667c87a9 chore(git): 补 desktop_runtime_new 忽略规则
f05313f2 chore(git): desktop_runtime 构建产物统一忽略（含 desktop_runtime_new）
d808c88c feat(ci): Stage 4 质量护栏 - shell-lint/install-smoke/version-check
c98c6a0a docs(deploy): 阶段3 文档可信度修订 v2.7.0
20bec62b build(desktop): build_desktop_runtime 用 clear_dir 替代 rmtree 以规避沙箱安全删除守卫
f25a34c6 docs(deploy): 阶段2 验证状态归档 + 修正 requirements torch/cadquery 依赖说明
6ff656d5 fix(desktop): 安装包首次启动链路修复（桌面实装验证发现 4 项）
97ff6a05 chore(mypy): 清理 3 处 unused-ignore（全仓 unused-ignore 归零）
cd61c3ad chore(git): 忽略测试运行动态产物目录（时间戳漂移）
393a28d3 fix(refactor): 修复拆分引入的 self 参数残留与缺失 import（关键正确性）
a94a3529 fix(gcode): 修复 _validation 抽取代码的 2 个 mypy 类型错误
be2efd6f refactor(api): 拆分 cam_validation 路由 Pydantic 模型（D5 God 模块）
739a65d9 refactor(cad): 拆分 cadquery_gen 模块级纯函数（D5 God 模块）
d442c3a2 refactor(services): 拆分 rl_agent_service 纯辅助方法（D5 God 模块）
cbc085b6 refactor: 拆分 resource_card_service 与 dreaming/closed_loop（D5 God 模块）
5f9d8064 refactor(services): 拆分 project_package_service 纯辅助方法（D5 God 模块）
c4abf26b refactor(tasks): 拆分 execution.py 模型与日志类（D5 God 模块）
1b1cd706 refactor(quality): 恢复并固化 God 模块拆分成果与桌面构建流水线
1523d240 refactor(python): 清理 mypy 基线建立后的无用 type:ignore 注释
2ea159ba fix(plugins): 补充 get_dependency_resolver 与 WorkerConfig 导入
17da3bce fix(deploy): 修复阶段2解耦后的安装链路并统一版本至 2.7.0
a138186e feat: V2.7.0 工程与研究模块解耦重构
d74a24ec feat: V2.6.0 架构重构与契约层建设
bb5978fa feat: V2.5.0 版本更新与架构优化
edb4a47d feat: V2.4.0 架构重构与功能增强
ebe988db feat: V2.3.0 代码质量优化与稳定性增强
b8d3f8f2 feat: V2.2.1 架构重构与功能增强
f5d1f071 feat: 多系统数控加工端到端验证与研究模块重构
340a6f5a feat: 添加完整文档站点和核心功能模块
81eac68f fix(ci): 修复 Python Full Test Suite 与 API 文档同步问题
c15a444b ci: 触发 3D重建几何精度验证 工作流重跑
89fc14da ci: 触发 3D重建几何精度验证 工作流重跑
e8f0a9be ci(geometry-validation): include workflow file in push paths filter
945fb42b fix(ci): add PYTHONPATH to geometry-validation workflow
8ac425d6 chore: sync workspace to remote main (upload staged files)
338d3bb8 fix: comprehensive security and code quality remediation (V1.12.1)
6b43db8a chore: cleanup backup files
aa2ed36c feat: add standard GitHub Actions health check workflow
20c021ff feat: add Agentic Workflows daily health check
ea80e628 chore: 添加配置文件备份
2864f1dc chore: 版本号统一更新为V1.11.0
1693fed4 fix: 补充提交遗漏的用户模型文件
09957237 feat: V1.10.0 企业级制造全流程自动化里程碑版本
ba6f5d86 feat: 集成STEP导入、刀路编辑器、仿真面板及规则编辑器等核心功能模块
1a7bca47 ci: 构建完整测试体系与CI流水线
d0184bc5 feat: NC仿真系统、错误分类体系、性能基准框架、工艺规划引擎、多CNC后处理器、几何验证系统及开源基础设施完善 (V1.8.0)
d23cc4ba docs: 添加Git LFS使用说明并更新CI/CD工作流
375e5cd5 chore: 将大型二进制文件迁移至Git LFS管理
24726d2d chore: 配置Git LFS跟踪规则管理大型二进制文件
b0d3f613 fix: 修复core模块异常类导入路径
e714b26f style: 应用Ruff格式修复到测试与预处理脚本
ce5bb45b docs: 新增文档、安全审计与日志系统
9ae164af test: 扩展测试框架与新增单元测试
3c5bb311 refactor: 优化前端组件与视图架构
32c95a95 feat: 增强RAG知识库系统与数据处理
f741c7a3 feat: 重构Python核心业务逻辑与API架构
bca410c6 feat: 升级LNN AI引擎核心架构与训练系统
3107a7bd feat: 实现国际化系统(i18n)与多语言支持
ec08d674 chore: upgrade build infrastructure and configuration to V1.7.1
180b9b78 fix(docs): replace placeholder username with actual owner in GitHub Actions badges
7e993da1 feat(devops): add version sync tooling and environment-aware CORS configuration
37f540e2 docs: rename CHANGELOG to Chinese filename for consistency
fe209c50 docs: release V1.7.0 changelog and update version numbers
8bb7b190 feat: add agent state management, plugin system, template evolution, goal alignment and governance
3ad764c9 Update README.md
1ce9e0b7 chore: 更新文档、配置与工具脚本
1ebbcacd test: 新增测试套件与诊断脚本
40b31dc4 feat(frontend): 新增Vue组件、视图与状态管理
eeb07cb4 feat(tauri): 增强桌面端安全与侧车管理
1e86a6e2 feat(api): 新增API端点、Agent网关、MCP服务与核心服务
33ab74d2 feat(ai): 重构LNN模型架构与AI推理训练系统
53b37af3 feat(core): 添加核心基础设施模块 - 认证、审计日志、权限、任务系统
579059bb chore: remove deprecated files and reorganize project structure
58a662c3 chore: add CNC machining datasets, UniWear dataset, and model files
e3994501 feat: add tests, deployment config, and project infrastructure files
4bb403b9 feat: update AI services, RAG, CAD, frontend, Tauri backend, and project configuration
71d22b7f feat: add LNN model caching, quantization, GPU training, SSE streaming, and LOD optimization
0d970d80 docs: 添加Git协作开发指南文档
89c74406 feat: 初始化灵境制造项目 - 首次提交
