# 灵境制造 - 开发文档

> 面向**项目内部开发者、贡献者、运维工程师**的技术参考手册
> **适用版本**：v2.5.0（与代码版本完全一致）
> 本目录包含架构说明、开发环境搭建、测试策略与贡献流程。

## 目录

| 文档 | 内容简介 | 适用人群 |
|------|----------|----------|
| [架构概述](./架构概述.md) | 系统分层架构、模块拓扑、核心组件交互流程、技术栈选型 | 架构师、新进开发者 |
| [开发环境搭建](./开发环境搭建.md) | 依赖版本、Python/Node/Docker 环境搭建、IDE 配置、常见问题 | 新进开发者、贡献者 |
| [测试指南](./测试指南.md) | 测试策略、单元/集成/E2E 测试执行、覆盖率要求、报告生成 | QA、开发者 |
| [贡献指南](./贡献指南.md) | 分支管理、提交规范、PR 流程、代码审查标准、行为准则 | 所有贡献者 |

## 1. 项目速览

- **项目代号**：灵境制造 LNN AI
- **仓库类型**：Monorepo（前端 + 后端 + 桌面壳）
- **核心特性**：
  - 液态神经网络（LNN）推理引擎（CFC / LTC / Hybrid）
  - DXF/STEP 图纸到 NC 代码端到端流水线
  - 规则引擎 + 机器学习融合（Dempster-Shafer 证据理论）
  - 插件化模板系统（ToolpathEditor、模板演化）
  - 智能体网关（Agent Gateway + Goal Alignment）
  - 多端形态：Web、Tauri 桌面、CLI

## 2. 技术栈一览

### 2.1 后端（python/）

| 类别 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.115+ |
| 异步运行时 | Uvicorn + asyncio |
| ORM | SQLAlchemy 2.x（异步） |
| 数据库 | SQLite 3.x（WAL 模式，嵌入式零运维，选型依据见 ADR-003） |
| 缓存/进度 | Redis 7 |
| 向量库 | ChromaDB |
| AI 推理 | ncps（LNN）、Ollama（LLM） |
| 3D / CAD | CadQuery、build123d |
| 测试 | pytest、pytest-asyncio、httpx |
| 代码质量 | ruff、black、mypy |

### 2.2 前端（src/）

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3.4+ + TypeScript 5.x |
| 构建 | Vite 6 |
| 状态 | Pinia |
| 路由 | Vue Router 4 |
| UI | Element Plus + 自研组件 |
| 3D | Three.js |
| HTTP | axios + SSE（EventSource） |
| 国际化 | vue-i18n |
| 测试 | Vitest、@vue/test-utils |

### 2.3 桌面（Tauri）

| 类别 | 技术 |
|------|------|
| 壳 | Tauri 2.x（Rust） |
| Python Sidecar | subprocess + IPC |
| 打包 | tauri-bundler（Windows MSI、macOS DMG、Linux AppImage） |

### 2.4 基础设施

| 类别 | 技术 |
|------|------|
| 容器化 | Docker + docker-compose |
| 编排 | Kubernetes（deploy/） |
| 监控 | Prometheus + Grafana |
| CI/CD | GitHub Actions |
| 注册表 | Harbor / ghcr.io |
| 镜像签名 | Cosign（Sigstore） |

## 3. 仓库结构

```
灵境制造（上线版）/
├── src/                       # 前端 Vue 3
├── python/                    # 后端 FastAPI
├── docs/                      # 本目录所属
├── deploy/                    # K8s / Prometheus
├── config/                    # 全局配置
├── tests/                     # Vitest 前端组件测试
├── src-tauri/                 # Tauri Rust 桌面壳
├── .github/workflows/         # CI
├── docker-compose.yml
├── pyproject.toml
├── package.json
└── README.md
```

## 4. 入口与快速跳转

- 后端 API 入口：`python/app/main.py`
- 前端应用入口：`src/main.ts`
- Tauri 入口：`src-tauri/src/main.rs`
- OpenAPI 规范：`docs/api/openapi.json`
- 部署清单：`deploy/k8s/`
- CI 工作流：`.github/workflows/ci.yml`

## 5. 阅读顺序建议

新进开发者建议按以下顺序阅读：

1. [架构概述](./架构概述.md) — 先建立全局认知
2. [开发环境搭建](./开发环境搭建.md) — 把代码跑起来
3. [测试指南](./测试指南.md) — 学会验证自己写的代码
4. [贡献指南](./贡献指南.md) — 提交第一份 PR
5. 用户手册（[docs/user-guide/](../user-guide/)）— 理解功能用例
6. API 文档（[docs/api/](../api/)）— 熟悉接口契约
