# 灵境制造 - 文档目录

> 本目录是项目文档的**单一源**（Source of Truth）。

## 目录定位

| 子目录 | 性质 | 说明 |
|--------|------|------|
| `user-guide/` | 对外 | 用户指南（安装、快速入门、功能详解、安全须知、故障排查） |
| `development/` | 对外 | 开发者文档（架构概述、开发环境搭建、测试指南、贡献指南），版本与代码同步 |
| `api/` | 对外 | API 参考（README、错误码、示例、OpenAPI 规范） |
| `ai/` | 对外 | AI 功能文档（贝叶斯 LNN、主动学习、自动重训练） |
| `simulation/` | 对外 | 仿真模块文档（颤振分析、切削力、集成指南） |
| `integrations/` | 对外 | 系统集成文档（OPC UA、MTConnect） |
| `变更摘要/` | 对外 | 版本变更日志（V2.5.0 → V2.7.0；V2.8.0 起以根 `CHANGELOG.md` 为单一真源） |
| `adr/` | 内部 | 架构决策记录（001-021：LNN 选型、FastAPI 选型、SQLite 选型、CAM 校验等） |
| `baseline/` | 内部 | 基线报告与基础设施问题记录 |
| `knowledge-graph/` | 内部 | 知识图谱本体定义与样本数据 |
| `operations/` | 内部 | 交付与测试规划（工业级交付路线图、测试补全规划） |
| `paper_and_competition/` | 内部 | 论文与大创赛材料（工程贡献叙事、实验数据模板） |
| `pipelines/` | 内部 | 数据采集流水线使用说明 |
| `prompts/` | 内部 | 提示词模板 |
| `rag/` | 内部 | RAG 文档解析说明 |
| `reports/` | 内部 | 各类审计/优化/安全报告 |
| `review/` | 内部 | 界面评审截图与评审输出（`review_outputs/`） |
| `runbook/` | 内部 | 运维手册（备份恢复、故障排查） |
| `security/` | 内部 | 安全测试报告模板 |
| `superpowers/` | 内部 | 历史规划与设计规格（plans/specs） |
| `workshop_landing_preparation/` | 内部 | 车间落地准备（控制器兼容性、颤振校准、试切检测，关联 ADR-019） |

## 与 `docs-site/` 的关系（E-36）

```
docs/                     ← 源（本目录，含全部内部 + 对外文档）
   │
   └── docs-site/docs/    ← VitePress 发布副本（仅镜像对外子集）
```

- **`docs/`** 是**源目录**，包含全部内部与对外文档。
- **`docs-site/docs/`** 是 VitePress 文档站的**发布副本**，仅镜像本目录中的对外子集：
  - 镜像目录：`ai/` `api/` `development/` `integrations/` `simulation/` `user-guide/` `wiki/`
  - 转换目录：`变更摘要/` → `changelog/`（命名规范化）
  - 新增目录：`index.md`（首页）、`.vitepress/`（站点配置）
- **同步规则**：修改对外文档时，先改 `docs/` 中的源文件，再同步到 `docs-site/docs/` 对应目录。
- **内部文档**（`adr/` `reports/` `research/` 等）不进入 `docs-site/docs/`，避免泄露。

## 顶层文件

- `api-reference.md`：API 参考（详细 OpenAPI 规范见 `api/openapi.json`）
- `国内部署指南.md`：国内部署指南（镜像加速、离线模型、Ollama 部署）
- `优化升级路线图-2026-08.md`：当前有效的产品优化路线图（A/B/C/D 四条线）
- `上线就绪审计-20260807.md`、`上线就绪评分报告-20260804.md`：上线就绪审计记录
- `版本管理策略.md`：版本与分支管理策略
- `cleaning_report.md`、`cleaning_summary_20260825.md`：仓库治理报告

> 历史/已执行完毕的计划文档（如 `REFACTOR_PLAN_V2.6.1.md`）收录于 `archive/`。
