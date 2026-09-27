# 灵境制造文档站

基于 VitePress 构建的专业文档站点，为灵境制造桌面级制造物理 AI 提供完整的用户指南、开发者文档和 API 参考。

## 与 `docs/` 的关系（E-36）

```
docs/                     ← 源目录（项目根，含全部内部 + 对外文档）
   │
   └── docs-site/docs/    ← VitePress 发布副本（仅镜像对外子集）
```

- **`docs/`**（项目根目录下的 `docs/`）是**源目录**，包含全部内部与对外文档。
- **`docs-site/docs/`**（本目录下的 `docs/`）是 VitePress 文档站的**发布副本**，仅镜像源目录中的对外子集：
  - 镜像目录：`ai/` `api/` `development/` `integrations/` `simulation/` `user-guide/` `wiki/`
  - 转换目录：`变更摘要/` → `changelog/`（命名规范化）
  - 新增目录：`index.md`（首页）、`.vitepress/`（站点配置）
- **同步规则**：修改对外文档时，先改 `docs/` 中的源文件，再同步到 `docs-site/docs/` 对应目录。
- **内部文档**（`docs/adr/` `docs/reports/` `docs/research/` 等）不进入本目录，避免泄露。

## 快速开始

### 安装依赖

```bash
cd docs-site
pnpm install
```

### 本地开发

```bash
pnpm dev
```

访问 http://localhost:5173 查看文档站点。

### 生产构建

```bash
pnpm build
```

构建产物将输出到 `dist/` 目录。

### 预览构建结果

```bash
pnpm preview
```

## 文档结构

```
docs-site/
├── docs/
│   ├── .vitepress/          # VitePress 配置
│   │   └── config.js        # 主题配置
│   ├── user-guide/          # 用户指南（镜像自 docs/user-guide/）
│   ├── development/         # 开发者文档（镜像自 docs/development/）
│   ├── api/                 # API 参考（镜像自 docs/api/）
│   ├── ai/                  # AI 功能文档（镜像自 docs/ai/）
│   ├── simulation/          # 仿真模块文档（镜像自 docs/simulation/）
│   ├── integrations/        # 系统集成文档（镜像自 docs/integrations/）
│   ├── wiki/                # 项目 Wiki（镜像自 docs/wiki/）
│   ├── changelog/           # 更新日志（转换自 docs/变更摘要/）
│   └── index.md             # 首页
├── scripts/                 # 自动化脚本
│   └── generate-api-docs.js # API 文档生成
└── package.json
```

## 文档分类

### 用户指南
面向最终用户的操作文档，包括安装、配置、使用教程等。

### 开发者文档
面向开发人员的技术参考手册，包含架构概述、开发环境搭建、测试指南与贡献流程。

### API 参考
基于 OpenAPI 规范的接口文档，包含错误码说明与使用示例。

### AI 功能
LNN 引擎、贝叶斯推理、主动学习与自动重训练相关文档。

### 仿真模块
颤振分析、切削力仿真与集成指南。

### 系统集成
OPC UA 与 MTConnect 集成说明。

### 项目 Wiki
12 篇主题文档，覆盖项目概览、整体架构、目录结构、后端核心、AI 引擎、业务能力、工程任务、安全认证、前端架构、数据基础设施、部署运行与关键 API 索引。

### 更新日志
从 V1.3.0 到 V2.3.0 的版本变更记录。

## 维护说明

- 修改对外文档时，**必须先修改 `docs/` 中的源文件**，再同步到本目录。
- 添加新的对外文档目录时，需同步更新 `.vitepress/config.js` 中的 `sidebar` 配置。
- 内部文档（ADR、报告、研究、运维手册等）仅放在 `docs/` 中，不进入本目录。

## 依赖锁文件提交策略（P2-E7）

- **策略**：`pnpm-lock.yaml` **应当提交**到仓库，以保证文档站构建可复现。
- **首次安装后**：执行 `pnpm install` 会生成 `pnpm-lock.yaml`，请将其一并提交。
- **依赖升级时**：先在本地验证 `pnpm build` 通过，再提交更新后的 `package.json` 与 `pnpm-lock.yaml`。
- **本地 .gitignore**：`docs-site/.gitignore` 仅忽略 `node_modules/` 与构建产物（`dist/`、`.vitepress/cache/`），不忽略 lockfile。
- **若需切换为"不锁版本"策略**：在根 `.gitignore` 中显式添加 `docs-site/pnpm-lock.yaml` 并同步更新本说明。
