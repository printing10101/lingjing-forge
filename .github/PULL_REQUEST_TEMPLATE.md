<!--
  感谢您为灵境制造提交 PR!请按以下结构填写。
  提交前请确认 CI 全部通过(lint + test + type-check)。
-->

## 变更概述

<!-- 简要描述本次变更的内容与目的,1-3 句话 -->

## 相关 Issue

<!-- 使用关键词关联:Fixes #123 / Resolves #123 / Related to #123 -->
Closes #

## 变更类型

请勾选适用的项(可多选):

- [ ] 新功能 (feat)
- [ ] Bug 修复 (fix)
- [ ] 文档更新 (docs)
- [ ] 代码重构 (refactor,不改变功能)
- [ ] 性能优化 (perf)
- [ ] 测试补充 (test)
- [ ] 构建/工具/CI (chore/build/ci)
- [ ] UI/UX 改进 (style)
- [ ] 安全相关修复 (security)

## 涉及模块

<!-- 勾选本次变更影响的模块,便于 Reviewer 评估范围 -->

- [ ] 前端 (Vue 3 / TypeScript / Three.js)
- [ ] Tauri 桌面外壳 (Rust / IPC / sidecar)
- [ ] 后端 API (FastAPI)
- [ ] AI 内核 (LNN / LLM Gateway / RAG / 知识图谱)
- [ ] 工艺规划 (特征识别 / 工序排序 / 物理验证)
- [ ] 后处理器 (Fanuc / Siemens / Heidenhain / …)
- [ ] DNC 集成 (MTConnect / OPC UA / MES)
- [ ] 仿真 (颤振 / 切削力 / 体素切削)
- [ ] 数据库 (SQLite / ChromaDB)
- [ ] CI/CD (.github/workflows)
- [ ] 文档 (docs/ / docs-site/)
- [ ] 其他:

## 变更详情

<!-- 详细说明:实现了什么、为什么这样实现、关键技术决策 -->

### 实现说明

### 技术决策(若适用)

<!-- 如有多种方案,说明为何选择当前方案 -->

### Breaking Changes

- [ ] 本次变更包含破坏性变更(若勾选,请在下方说明迁移路径)

<!-- 描述破坏性变更与迁移路径 -->

## 测试

请勾选已完成的测试项(至少完成一项):

- [ ] 已通过前端单元测试 (`pnpm test:run`)
- [ ] 已通过 Python 后端测试 (`python -m pytest python/tests`)
- [ ] 已通过 TypeScript 类型检查 (`pnpm type-check`)
- [ ] 已通过 ESLint / Prettier / Black / Ruff / cargo fmt 检查
- [ ] 已添加新的测试用例
- [ ] 已通过前端组件测试 (`npx vitest run`, UI 变更时必须)
- [ ] 已手动测试核心场景
- [ ] 已测试边界条件与异常路径

### 测试说明

<!-- 简要描述测试方法与结果,如新增了哪些测试用例 -->

## 截图 / 录屏

<!-- UI/UX 变更或新功能演示请附截图或录屏,便于直观评估 -->

## 检查清单

提交前请确认:

- [ ] 代码风格符合项目规范(ESLint / Prettier / Black / Ruff / cargo fmt / clippy)
- [ ] 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式
- [ ] 文档已同步更新(API 变更需更新 `docs/api/`,行为变更需更新用户手册)
- [ ] 已测试边界条件与异常情况
- [ ] 与 `main` 分支无合并冲突
- [ ] 已移除调试代码(console.log / print / debugger / TODO 临时注释)
- [ ] 新增依赖已评估许可证兼容性(本项目为 Apache 2.0)
- [ ] 安全相关:未引入硬编码密钥/令牌,未降低现有安全机制

## Reviewer 提示

<!-- 提示 Reviewer 需要重点关注的部分,如:复杂算法、并发处理、安全敏感操作 -->

---

感谢您的贡献!提交后请关注 CI 状态,如有失败请及时修复。
