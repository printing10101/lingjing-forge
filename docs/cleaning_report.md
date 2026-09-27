# 代码库整洁性审查与清理报告

**审查日期**: 2026-08-25  
**审查范围**: 灵境制造（上线版）主项目  
**审查目标**: 识别并清理非必要保留文档/文件

---

## 清理清单

### 1. 根目录诊断探针文件（可安全删除）

| 文件名 | 大小 | 说明 | 建议 |
|--------|------|------|------|
| `_agent_state_test.py` | 5.5KB | Agent 状态测试探针 | 删除 |
| `_perm_audit.py` | 1.4KB | 权限审计探针 | 删除 |
| `_probe_out2.log` | 313KB | 探针输出日志 | 删除 |
| `_probe_out3.log` | 303KB | 探针输出日志 | 删除 |
| `_probe_out4.log` | 4.3KB | 探针输出日志 | 删除 |
| `_probe_out5.log` | 1.1MB | 探针输出日志 | 删除 |
| `_probe_out6.log` | 8.5KB | 探针输出日志 | 删除 |
| `_probe_sidecar.py` | 3.3KB | Sidecar 探针脚本 | 删除 |
| `_probe_sidecar2.py` | 2.0KB | Sidecar 探针脚本 | 删除 |
| `_probe_sidecar3.py` | 1.5KB | Sidecar 探针脚本 | 删除 |
| `_probe_sidecar4.py` | 0.8KB | Sidecar 探针脚本 | 删除 |
| `_probe_sidecar5.py` | 2.1KB | Sidecar 探针脚本 | 删除 |
| `_rb_test.py` | 1.9KB | RB 测试探针 | 删除 |
| `_routes.txt` | 30.5KB | 路由提取文本 | 删除 |
| `_route_audit.py` | 1.3KB | 路由审计探针 | 删除 |
| `_route_reflect.py` | 1.1KB | 路由反射探针 | 删除 |
| `_smoke_test_tmp.py` | 5.9KB | 冒烟测试临时文件 | 删除 |

**小计**: 17 个文件，约 2.2MB

---

### 2. 临时/备份文件

| 文件名 | 大小 | 说明 | 建议 |
|--------|------|------|------|
| `创建快捷方式.bat` | 0.6KB | 快捷方式创建脚本（非项目文件） | 删除 |
| `创建快捷方式.ps1` | 0.4KB | 快捷方式创建脚本（非项目文件） | 删除 |
| `~` | - | Vim 交换文件（临时） | 删除 |

**小计**: 2 个文件

---

### 3. 临时目录（可安全删除）

| 目录名 | 说明 | 建议 |
|--------|------|------|
| `.dsh-workspaces/` | DSH 同步配置目录 | 删除 |
| `.audit_tmp/` | 审计临时目录 | 删除 |
| `.tmp_lam_extract/` | Docx 解压临时目录 | 删除 |
| `.tmp_lam_extract5/` | Docx 解压临时目录 | 删除 |

**小计**: 4 个目录

---

### 4. 工程目录备份文件（.bak）

#### 4.1 engineering/python/app/ 备份

| 路径 | 说明 | 建议 |
|------|------|------|
| `engineering/python/app/api/routers/engineering.py.bak` | 路由备份 | 删除 |
| `engineering/python/app/api/routers/__init__.py.bak` | 包备份 | 删除 |
| `engineering/python/app/contracts/__init__.py.bak` | 包备份 | 删除 |
| `engineering/python/app/database/models/__init__.py.bak` | 模型备份 | 删除 |
| `engineering/python/app/dxf/pipeline.py.bak` | 管道备份 | 删除 |
| `engineering/python/app/dxf/__init__.py.bak` | 包备份 | 删除 |
| `engineering/python/app/integrations/mtconnect/experience_bridge.py.bak` | 桥接备份 | 删除 |
| `engineering/python/app/integrations/mtconnect/__init__.py.bak` | 包备份 | 删除 |
| `engineering/python/app/parametric_geometry/pipeline.py.bak` | 几何管道备份 | 删除 |
| `engineering/python/app/parametric_geometry/__init__.py.bak` | 包备份 | 删除 |
| `engineering/python/app/postprocessor/dialect/registry.py.bak` | 方言注册备份 | 删除 |
| `engineering/python/app/postprocessor/dialect/__init__.py.bak` | 包备份 | 删除 |

#### 4.2 engineering/python/desktop_runtime/backend/app/ 备份

| 路径 | 说明 | 建议 |
|------|------|------|
| `engineering/python/desktop_runtime/backend/app/api/routers/engineering.py.bak` | 运行时路由备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/api/routers/__init__.py.bak` | 运行时包备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/contracts/__init__.py.bak` | 运行时包备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/database/models/__init__.py.bak` | 运行时模型备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/dxf/pipeline.py.bak` | 运行时管道备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/dxf/__init__.py.bak` | 运行时包备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/integrations/mtconnect/experience_bridge.py.bak` | 运行时桥接备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/integrations/mtconnect/__init__.py.bak` | 运行时包备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/parametric_geometry/pipeline.py.bak` | 运行时几何备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/parametric_geometry/__init__.py.bak` | 运行时包备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/postprocessor/dialect/registry.py.bak` | 运行时方言备份 | 删除 |
| `engineering/python/desktop_runtime/backend/app/postprocessor/dialect/__init__.py.bak` | 运行时包备份 | 删除 |

#### 4.3 测试目录备份文件

| 路径 | 说明 | 建议 |
|------|------|------|
| `engineering/python/tests/unit/test_dialect_declared_hooks_phaseE.py.bak` | 测试备份 | 删除 |
| `engineering/python/tests/unit/test_mtconnect_experience_bridge.py.bak` | 测试备份 | 删除 |
| `engineering/python/tests/unit/test_sovereignty_ratio.py.bak` | 测试备份 | 删除 |

#### 4.4 CADQuery 包备份

| 路径 | 说明 | 建议 |
|------|------|------|
| `engineering/python/.venv/Lib/site-packages/cadquery_ocp-7.9.3.1.1.dist-info/WHEEL.bak` | 包元数据备份 | 删除 |

**小计**: 30 个 .bak 文件

---

### 5. subagent-source/ - 独立的 DeepSeek Harness 框架项目

| 说明 | 详情 | 建议 |
|------|------|------|
| **项目性质** | 完整的 TypeScript/Python 框架项目 | **需用户决策** |
| **代码规模** | 约 440,000+ 行代码 | |
| **与灵境制造关系** | 无关，属于同一用户下的另一独立项目 | |
| **当前状态** | 混入灵境制造工作区 | |

**选项**:
1. **完全移除**: `rm -rf subagent-source/`（如果不需要）
2. **独立迁移**: 移至独立目录 `~/deepseek-harness/`
3. **保留但有组织**: 确认是否属于当前审查范围

**建议**: 确认是否保留，如果不保留则批量删除（约 7351 个文件）

---

### 6. docs/ 目录文档审查

#### 6.1 架构决策记录 (ADR) - 必要保留 `docs/adr/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `ADR-001~021.md` | 架构决策记录 | 保留 |
| `ADR-TEMPLATE.md` | 模板文件 | 保留（供未来使用） |
| **小计**: 22 个文件 | | |

#### 6.2 开发文档 - 必要保留 `docs/development/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `README.md`, `架构概述.md`, `开发环境搭建.md` | 核心开发文档 | 保留 |
| `测试指南.md`, `贡献指南.md` | 协作文档 | 保留 |
| `自主化与护城河路线图.md` | **执行手册** | 保留 |
| `接线指南-v3-总集成.md` 等 | 接线文档（历史但最终版） | 保留 |
| **小计**: 约 25 个文件 | | |

#### 6.3 用户指南 - 必要保留 `docs/user-guide/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `快速入门.md`, `功能详解.md`, `安装指南.md` | 用户体验文档 | 保留 |
| `安全须知.md`, `故障排查.md` | 安全与支持 | 保留 |

#### 6.4 API 文档 - 必要保留 `docs/api/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `README.md`, `error-codes.md`, `examples.md` | API 参考 | 保留 |

#### 6.5 报告文档 - 部分保留 `docs/reports/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `PROJECT_STATUS.md` | 项目状态跟踪 | 保留 |
| `SECURITY_INTEGRITY_FIX_REPORT.md` | 安全修复报告 | 保留 |
| `ERROR_HANDLING.md` | 错误处理规范 | 保留 |
| `ROUTE_A_FIX_REPORT.md` | **历史修复报告** | 评估 |
| `ACADEMIC_REVIEW_REPORT.md` | **学术论文审查报告** | 评估 |
| `DEPLOYMENT_CHECKLIST.md` | 部署检查清单 | 保留 |

#### 6.6 变更摘要目录 - 历史版本迭代 `docs/变更摘要/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `变更摘要 V1.3.0~2.7.0.md` (17 个) | 历史版本变更记录 | **可归档** |
| **风险评估**: 仅保留最近的 3 个版本（V2.5.0, V2.6.0, V2.7.0） | | |

#### 6.7 review_outputs/ - 评审过程文件 `docs/review_outputs/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `hermes_review_vX*.md` (8 个) | Hermes 评审过程版本 | **删除** |
| `review_vX_run*.md` (10+ 个) | 内部评审过程版本 | **删除** |
| **说明**: 仅保留最终评审结论，过程草稿无需保留 | | |

#### 6.8 工艺验证文档 - 现场实施 `docs/workshop_landing_preparation/`

| 文件 | 说明 | 状态 |
|------|------|------|
| `P3-1~5.md` (5 个) | 车间落地验证步骤 | 保留（现场参考） |

#### 6.9 论文草稿 - 学术研究 `docs/LAM_chatter_paper*.md`

| 文件 | 说明 | 状态 |
|------|------|------|
| `LAM_chatter_paper_draft_v1_zh.md` | **V1 论文草稿** | 删除（已被 v2 替代） |
| `LAM_chatter_paper_draft_v2_zh.md` | **正式论文草稿** | 保留 |
| `LAM_chatter_suppression_paper_plan.md` | 论文计划 | 保留 |

#### 6.10 docs-site/ - 文档网站建设文件

| 文件 | 说明 | 状态 |
|------|------|------|
| `docs-site/**.md` | 文档网站源码（与 docs/ 内容镜像） | **评估** |
| **说明**: 如果 docs/ 已足够，docs-site/ 可精简为仅必要的 | | |

#### 6.11 根目录文档文件 - 必要保留

| 文件 | 说明 | 状态 |
|------|------|------|
| `docs/README.md` | 文档索引 | 保留 |
| `docs/api-reference.md` | API 参考索引 | 已修改（M 状态） |
| `docs/启动检查报告.md` | 启动验证报告 | 保留 |
| `docs/前端交互修复报告.md` | 前端修复报告 | 保留 |
| `docs/前端交互修复 - 验证清单.md` | 前端验证清单 | 保留 |
| `docs/上线就绪评分报告 -20260804.md` | **上线前评估** | 历史版本 |
| `docs/上线就绪审计 -20260807.md` | **上线前审计** | 历史版本 |
| `docs/真实数据验证现状.md` | 验证状态跟踪 | 保留 |

---

## 清理统计数据

### 可立即删除的文件

| 类型 | 数量 | 预估空间 |
|------|------|---------|
| 诊断探针文件（根目录） | 17 个 | ~2.2MB |
| 临时/备份文件（根目录） | 2 个 | <1KB |
| 临时目录 | 4 个 | - |
| 工程备份文件 (.bak) | 30 个 | - |
| **小计** | **53 个文件 + 4 个目录** | **~2.2MB** |

### subagent-source/

| 类型 | 数量 | 预估空间 |
|------|------|---------|
| 独立项目文件 | 7351 个 | ~200MB+ |
| **说明** | 需用户决策是否保留 | |

### docs/ 目录文档评估

| 类型 | 保留 | 删除/归档 | 说明 |
|------|------|----------|------|
| ADR | 22 个 | 0 | 架构决策，全部保留 |
| 开发文档 | 25 个 | 0 | 核心开发文档，全部保留 |
| 用户指南 | 6 个 | 0 | 用户体验，全部保留 |
| API 文档 | 3 个 | 0 | API 参考，全部保留 |
| 报告 | 4 个 | 2 个 | ACADEMIC_REVIEW 临时评估 |
| 变更摘要 | 3 个 | 14 个 | 仅保留最近 3 版本 |
| review_outputs | 0 个 | 18+ 个 | 评审过程文件，全部删除 |
| workshop | 5 个 | 0 | 现场验证，保留 |
| 论文 | 2 个 | 1 个 | 删除 v1 草稿 |
| **总计** | **65 个** | **35 个+** | |

---

## subagent-source/ 审查结论

**关键发现**:
- `subagent-source/` 是 **DeepSeek Harness 框架** 的完整项目（约 44 万行代码）
- 与 **灵境制造（上线版）** 完全独立，无代码/文档共享
- 可能是用户手动复制或工具创建时的误入

**建议**:
1. **确认身份**: 这是用户另一独立项目，还是误操作？
2. **清理方案**:
   - 如果不需要 → 删除整个目录（7351 文件）
   - 如果需要 → 移至独立工作区 `~/deepseek-harness/`
   - 如果混淆 → 创建 `project-archive/` 子目录归并整理

---

## 执行计划

### 第一阶段（立即执行）：清理诊断探针和备份文件

1. **删除根目录诊断探针文件** (17 个)
   - `_agent_state_test.py`, `_perm_audit.py`, `_probe_*.py`, `_probe_out*.log`, `_routes.txt` 等

2. **删除临时目录** (4 个)
   - `.dsh-workspaces/`, `.audit_tmp/`, `.tmp_lam_extract*`

3. **删除备份文件** (30 个 .bak)
   - `engineering/python/app/**/*.bak`
   - `engineering/python/desktop_runtime/**/**/*.bak`
   - `engineering/python/tests/**/*.bak`

4. **删除快捷方式脚本** (2 个)
   - `创建快捷方式.bat/ps1`

### 第二阶段（需确认）：处理 subagent-source/

- **选项 A**: 删除（如果不需要）
- **选项 B**: 迁移至独立目录
- **选项 C**: 保留（如果属于同一用户工作区）

### 第三阶段（可选）：文档审查与整理

1. **删除 review_outputs/** (18+ 个评审过程文件)
2. **归档变更摘要** (保留最近 3 个版本)
3. **删除论文 v1 草稿**
4. **评估 docs-site/** 是否必要

---

## 注意事项

1. **subagent-source/** 的决策涉及 7000+ 文件，需特别谨慎
2. **docs/review_outputs/** 和 **docs/变更摘要/** 包含历史痕迹，删除前确认无追溯需求
3. **`.dsh-memory/`** 是工作记忆系统，已存在于根目录并跟踪，**勿删除**
4. 执行删除后运行 `git status` 验证工作区状态

---

**审查完成时间**: 2026-08-25  
**下一步**: 等待用户确认清理方案后执行
