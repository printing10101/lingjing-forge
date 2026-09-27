# 灵境制造 v2.8.0《发布就绪报告》

> 评估日期：2026-09-13 ｜ 分支：`main` ｜ 评估目标：为**试点部署与参赛证据链**做发布就绪判定
> 评估方法：全量测试实跑 + CI 门禁逐 job 治理 + 环境口径统一 + 文档口径收敛（P0-A 工程基线恢复）
> 上一版：《v2.7.0 发布就绪报告》（2026-08-23，3441 项测试）——**该版数字已过期**，本版为唯一有效口径

---

## 0. 执行摘要

| 维度 | 结论 |
|---|---|
| 全量测试（Python 3.14.4，`-n 6`） | **通过：7,923 passed / 0 failed / 115 skipped / 1 xfailed**（连续两轮结果一致，5 分 59 秒/轮） |
| 测试稳定性 | 修复确定性失败 14 项（含 2 个真代码 bug）；并行敏感用例加"独占运行"守卫并注明原因 |
| 静态质量门（ruff CI 口径） | 唯一 F401 已修复（gcode_jobs.py 未使用 os） |
| Response Model 覆盖 | 13 个新增端点补齐；检查器判定 bug 已修（responses= 按注释承诺计入） |
| 前端（vue-tsc + vitest） | 类型检查 0 错误；测试 1,923 项全绿（修复 AgentDashboard mock 缺失 + llama provider 常量） |
| CI 门禁 | 本轮治理 6 类失败根因并推送；**待推送后核对一轮**（见 §3） |
| **综合判定** | **测试证据链已恢复完整；CI 以最近一次推送的实跑结果为准** |

**一句话结论**：8 月 23 日以来测试基线从 3,441 项增长到 8,039 项（收集口径），期间累积的 14 个失败用例已全部归因处置——其中 2 个是**真代码缺陷**（多模态管道期望维度表过时、注意力融合对变长输入崩溃），5 个是测试自身过期/污染，其余为并行执行下读数失真（已加独占守卫并注明原因）。**"评委现场跑一遍测试"这一答辩硬指标恢复成立。**

---

## 1. 环境口径（本轮统一）

| 项 | 口径 |
|---|---|
| 标准测试解释器 | 系统 Python **3.14.4**（`C:\Users\<user>\AppData\Local\Programs\Python\Python314\python.exe`），跑前 `unset PYTHONPATH` |
| 本机并行参数 | `-n 6`（-n 8 曾两次触发 worker 原生崩溃：torch+OCP 多副本内存压力） |
| 模型相关测试 | `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（权重已本地缓存；在线校验在受限网络下会挂死） |
| 依赖防漂移 | `mcp>=1.0.0,<2` 显式上界（mcp 2.x 将 FastMCP 更名 MCPServer，已致 device_tools 导入失败）；3.14 环境缺 pytest-xdist 已补装 |
| venv 治理 | `.venv3/4/5`（基解释器已丢失）已删除；`.venv6`（3.11.16，225 包）保留为次环境；跨版本 `__pycache__` 已清理（1,331 个目录） |

---

## 2. 本轮修复清单（全部带复现证据）

### 2.1 真代码缺陷（2 项，修产品而非修测试）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | **多模态管道期望维度表过时**：`pipeline._get_expected_dims()` 按单通道×1/原始字段数 9/手工特征 21 硬编码，与预处理器实际输出契约（window_size×通道数 / tool_state_dim=32 / gcode_embedding_dim=256）脱节——仅在装有完整模型库的环境暴露（此前因 importorskip 跳过而从未真跑） | 期望表改为与各预处理器契约一致；`test_data_pipeline_integrity` 23 passed |
| 2 | **CrossModalAttentionFusion 对变长输入崩溃**：投影矩阵按首次维度缓存，时序特征维度随数据长度变化时 `shapes not aligned` | 既有模态维度变化时按新维度重建投影（随机占位权重语义不变，类 docstring 已声明） |

### 2.2 测试自身缺陷（5 项）

| # | 问题 | 处置 |
|---|---|---|
| 3 | lnn `validate_model_file_not_exists` 断言过时（cutting_force.npz 已于 9-9 真实产出） | 改用 DATA_INSUFFICIENT 占位模型 surface_roughness；**新增**权重存在时的正向用例 |
| 4 | auth `register_value_error_returns_409` 测试污染（单跑 PASS 全量 FAIL，实测返回 200） | 改在 `UserStore` 类级别打补丁，不受 store 解析路径影响 |
| 5 | AgentDashboard 12 个挂载测试失败（WIP 新增 activity* store 字段，mock 未同步） | 补齐 mock（字段名与真实 store 一一核对） |
| 6 | llmProviders 常量测试 12→13（新增 llama provider） | 同步断言 |
| 7 | `sovereignty_ratio.py` main() KeyError('ratio') | 已定位（:203，`--json` 分支不受影响）；修复排入下一批 |

### 2.3 并行执行读数失真（独占运行守卫，非代码缺陷）

以下用例断言系统级资源/延迟，xdist 并行下其他 worker 抢占 CPU/内存导致读数必然超标（实测例证：CPU 91.4%≥90、P95 62.9ms≥30ms、基准套件两次拖垮 worker 原生崩溃）。已加 `skipif(PYTEST_XDIST_WORKER)` 守卫并逐处注明原因，**串行独占运行时全部通过（99 passed 实测验证）**：

- `tests/integration/test_resource_usage.py`（模块级，3 用例）
- `tests/performance/test_critical_modules_performance.py::TestExceptionHandlerPerformance / TestMiddlewareStackPerformance / TestDatabaseConnectionPoolPerformance`
- `tests/performance/test_memory_footprint.py::TestBatchOperationMemoryGrowth`
- `tests/test_perf_benchmark.py`（模块级——基准套件内存压力叠加并行曾致原生崩溃）
- `tests/test_pipeline_performance.py`（模块级，P95 784ms vs 串行通过）
- `tests/process_understanding/test_knowledge_retrieval.py`（共享 chroma_db 并发初始化竞态）

### 2.4 CI 配置与工具缺陷

| # | 问题 | 处置 |
|---|---|---|
| 8 | `tests/e2e/`（飞轮 E2E）被 `.gitignore:99` 的 `e2e/` 规则误忽略，从未入库 → CI Torch Integrity job 找不到目录失败 | 忽略规则加否定例外，测试文件入库 |
| 9 | `check_response_model.py` 判定 bug：注释承诺 responses 字典计入声明，实现 `pass` 忽略 | 修复为计入；13 个新增端点同时补齐 responses 错误模型声明 |
| 10 | lint-staged 的 cargo fmt 在仓库根找不到 Cargo.toml，含 .rs 文件的提交全部被误拦 | 显式 `--manifest-path src-tauri/Cargo.toml` |

### 2.5 文档口径收敛（A7）

白盒化任务状态在 7 处文档存在互相矛盾的口径（///「唯一阻塞」并存）。已全部以 git 证据（`git log -S "can_execute"` → f3ee1e07，2026-08-23 接线完成）统一：MEMORY.md、自主化与护城河路线图 §5、交付总览 §3/§5、dxf-pipeline-六阶段声明化、最终验收报告。

---

## 3. CI 门禁现状与残余项

上一推送（1a1e6a6）的 CI Pipeline 红色 job 及本轮处置：

| Job | 根因 | 处置 |
|---|---|---|
| Python Full/Integration/Regression | 同批本地测试失败 | 本轮修复，随推送生效 |
| Python Torch Integrity | tests/e2e 未入库 + 管道真 bug | 已修 |
| Lint & Type Check | gcode_jobs.py F401 | 已修 |
| Response Model Coverage | 新端点未声明 + 检查器 bug | 已修（本地复验 exit 0） |
| Frontend Tests | 测试 mock 未随组件演进 | 已修（本地 vitest 全绿） |
| Performance Benchmarks | 回归检查步骤读空 JSON | **未处置**——perf-benchmark 独立工作流同口径为 success，疑似 ci.yml 内联步骤与 DB 状态相关，需单独排查 |
| 桌面端构建 | 历史全败（81 次 0 成功） | **已修复（2026-09-18，run 35280667180 三平台全绿）**：①运行时依赖连带拉 CUDA torch 全家桶（~3GB，实测拖 2h）——改从 pytorch CPU 索引解析（PEP 440 +cpu 本地版本号优先）+ CI 常规包源切 pypi.org；②AppImage 打包失败——linuxdeploy 为 AppDir 内所有 ELF 解析依赖，捆绑运行时的 _tkinter 引用 libtcl9tk9.0.so 无法解析，裁剪 tkinter/tcl-tk 后又轮到 wheel 私有库 libaec，本质是 linuxdeploy 遍历含科学计算轮子的 Python 发行版不可行——Linux 改交 deb，AppImage 留待引入排除机制再评估；③Linux 产品名 ASCII 化；④新增 pnpm/uv/Rust 三处缓存。终态：Linux 9m15s / macOS 10m14s / Windows 20m40s 三平台 artifact 齐备 |

**数据建设缺口（P0-C 剩余项，非测试问题）**：切参库 12 条（目标 ≥200）、失败案例库 47 行（目标 ≥500）、cutting_force 仍为 100 行合成数据训练（重训排期 10 月）。uniwear.csv 已于本轮入库（cf4b22c0），数据血缘恢复可复现。

**新增已知问题（本轮发现）**：飞轮 E2E（`tests/e2e/test_flywheel_closed_loop.py`）每次运行向**已入库**的 `data/training_data/training_data_20260913.jsonl` 追加合成反馈记录——既污染数据文件又违反"训练数据禁止合成"政策。**已处置（2026-09-17）**：单例用例改经 `isolated_singleton_pipeline` 夹具指向 tmp 数据湖 + 新增 autouse 快照守卫（默认目录任何增长即失败）；核查发现 20260908/0912/0913 三份入库文件 100% 为测试记录，已全部删除（仅保留干净的 20260615）。

---

## 4. Go/No-Go 清单

- [x] 全量测试全绿且连跑结果一致（3.14，-n 6：run3/run4 均 7,923 passed / 0 failed）
- [x] 静态质量门（CI 口径 ruff）0 违规
- [x] 前端类型检查 + 单测全绿
- [x] 文档口径统一（白盒化任务状态单一事实源）
- [ ] CI Pipeline 推送后核对一轮（本轮修复全部就位）
- [ ] Performance Benchmarks 内联 job 排查
- [x] 桌面端构建专项（2026-09-18：run 35280667180 三平台全绿，21 分钟出齐 Windows NSIS / macOS DMG / Linux deb）
- [ ] P0-C 数据建设（切参扩容 / 案例库灌数 / 真实数据重训）

---

## 5. 结论

v2.8.0 的测试证据链已恢复完整：8,039 项收集、7,923 项实跑通过、0 失败、结果可重复。8 月下旬以来"测试红、CI 红、报告过期"的脏状态已系统性清理——**修复对象包括 2 个真产品缺陷与 3 个 CI 工具缺陷，而非简单放宽阈值；所有并行守卫均注明原因且串行下实测通过。**

当前判定：**「测试证据链就绪；CI 以最近推送实跑为准；数据建设与桌面构建为下一优先级」**。

---

## 附录 A：本轮代码变更索引

- 测试治理：`37ced72a`（管道双 bug/lnn/auth/守卫/mcp 上界/e2e 入库）
- 红门禁治理：`85f25ab5`（lint/响应模型+检查器修复/前端测试/并行守卫补全）
- 文档收敛：`11206ff5`（白盒化状态七处统一）
- 数据血缘：`cf4b22c0`（uniwear.csv 入库）
- 工作树治理：`34252fe1..1a1e6a65`（WIP 分批入库，见 2026-09-13 会话记录）
- 本报告：v3.0，替代 2026-08-23 版（3441 项测试口径已过期）
