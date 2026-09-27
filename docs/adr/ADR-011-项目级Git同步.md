# ADR-011: 项目级 Git 同步

**日期**: 2026-07-14
**状态**: 已接受
**决策者**: 灵境制造团队

---

## 背景

阶段 6 p6-1（工作流模板市场）已完成，用户可发布/订阅/评分工作流模板。阶段 6 目标"生态内容飞轮"的下一环是 p6-2：让用户能在项目级别同步数据集/模型/配置/工作流，支持团队协作与版本追溯。

当前现状：

1. **ProjectStore 是 .vrm ZIP 包格式**（`app/projects/project_store.py`），资源类型仅 6 种 CAD/制造类型（drawing/model/toolpath/simulation/postprocessor/extension），与 ADR-005 阶段 2 引入的资源体系（dataset/model_artifact/workflow/config/snapshot）完全脱节。
2. **资源引用是文件名 + ZIP 内相对路径**，不是 URI（如 `dataset://<id>/<version>`），无法做内容寻址同步。
3. **无 content_hash 字段**（仅 `DatasetVersion.content_hash` 有），无法做内容寻址同步。
4. **模型无 ORM 持久化**：`LNNModelRegistry` 是内存单例 + 文件路径（`models/*.pt`），无版本表。
5. **ConfigSpec 无 DB 持久化**：仅内存 + YAML 文件。
6. **无 Git 写入能力**：仅有 `app/observability/git_collector.py`（只读采集 git_sha），commit/push/pull/clone 全部需从零实现。
7. **ExperimentSnapshot 已有 git_sha + code_dirty 字段**，与 Git 同步天然对齐。

p6-2 需要从零构建项目级 Git 同步能力，让用户能将项目（含数据集/模型/配置/工作流/快照/模板）通过 Git 进行版本控制与团队协作。

## 决策

采用"契约层 + 数据库 + 服务层 + 路由层 + 迁移 + 前端"六层落地项目级 Git 同步，**不修改现有 ProjectStore（.vrm ZIP 包保留给离线 CAD 工程包）**，新建独立的 `ProjectSyncService` 管理可同步项目：

1. **新增契约** `app/contracts/project_sync.py`：定义 `ResourceType` / `SyncStrategy` / `SyncStatus` 枚举 + `ResourceRef` / `ProjectSyncManifest` / `SyncRecord` dataclass。
2. **新增数据库表** `app/database/models/project_sync.py`：3 张表
   - `project_repos`：项目仓库主表（project_id / name / repo_path / remote_url / current_branch / current_commit / status）
   - `project_resource_refs`：资源引用表（project_id / resource_type / resource_uri / content_hash / sync_strategy / metadata）
   - `project_sync_records`：同步记录表（project_id / direction / commit_sha / status / message / timestamp）
3. **新增服务层** `app/services/project_sync_service.py`：封装 Git 操作（init/commit/push/pull/clone/status）+ 资源引用管理 + 同步记录，使用 `subprocess.run(["git", ...])` 调用系统 git（不引入 gitpython 依赖），线程安全（`threading.Lock`）。
4. **新增路由** `app/api/v1/project_sync.py`：REST 端点 prefix `/api/v1/project-sync`。
5. **新增 Alembic 迁移** `alembic/versions/<hex>_add_project_git_sync.py`：创建 3 张表 + 索引。
6. **前端契约** `src/contracts/project_sync.ts`：TS 类型与后端 dataclass 对齐。
7. **前端 Store** `src/stores/projectSync.ts`：Pinia store 对接 11 个端点。

### 同步策略设计

资源同步策略（`sync_strategy` 字段）根据资源类型与大小自动选择：

| 策略 | 含义 | 适用资源 |
|------|------|----------|
| `git_tracked` | 直接入 Git（文本文件） | YAML 配置、JSON 清单、workflow spec、project.yaml |
| `hash_referenced` | 仅记录 content_hash，实际数据通过 content-addressable storage 共享 | 数据集内容（.jsonl）、模型文件（.pt）、快照二进制 |
| `git_lfs` | 通过 Git LFS 跟踪（可选，需用户配置 LFS） | 中等大小文件（10MB-1GB） |

### 资源类型与 URI 设计

| 资源类型 | URI 格式 | content_hash 来源 |
|----------|----------|-------------------|
| `dataset` | `dataset://<dataset_id>/<version>` | `DatasetVersion.content_hash`（已有） |
| `model` | `model://<model_name>/<version>` | 文件 sha256（服务层计算） |
| `workflow` | `workflow://<run_id>` | spec JSONB sha256（服务层计算） |
| `config` | `config://<spec_name>` | YAML 文件 sha256（服务层计算） |
| `snapshot` | `snapshot://<snapshot_id>` | `ExperimentSnapshot.git_sha`（已有） |
| `template` | `template://<template_id>/<version>` | manifest_snapshot sha256（服务层计算） |

### Git 操作实现

使用 `subprocess.run(["git", ...])` 封装，不引入 gitpython 依赖：

- `init`：`git init` + 创建 `.lomo-project.yaml` 清单 + 初始 commit
- `status`：`git status --porcelain` + `git rev-parse HEAD` + `git rev-list --count @{u}..HEAD`（ahead）+ `git rev-list --count HEAD..@{u}`（behind）
- `commit`：检测资源 hash 变化 → 更新清单 → `git add` + `git commit -m`
- `push`：`git push origin <branch>`
- `pull`：`git pull origin <branch>`
- `clone`：`git clone <url>` + 解析 `.lomo-project.yaml`

## 理由

### 考虑的方案

1. **方案 A: 扩展现有 ProjectStore，把 .vrm ZIP 改为 Git 仓库**
   - 优点：复用现有 ProjectManifest 数据结构
   - 缺点：.vrm 是 ZIP 二进制包，与 Git 文本追踪哲学冲突；资源类型需大改（6 种 CAD 类型 → 6 种 ADR-005 资源类型）；破坏现有 CAD 工程包语义；现有用户已创建的 .vrm 工程将不兼容

2. **方案 B: 新建独立的 ProjectSyncService，不修改现有 ProjectStore**（**已选**）
   - 优点：语义清晰（.vrm 是离线工程包，Git 是在线协作同步）；可独立演进；与 ADR-005 资源 URI 体系对齐；不破坏现有 ProjectStore；前端可独立设计同步 UI
   - 缺点：代码量略大（新增 1 个契约 + 1 个服务 + 1 个路由 + 3 张表 + 前端契约/store + Alembic 迁移）

3. **方案 C: 完整方案（方案 B + model_artifacts 表 + config_specs 表 + 冲突解决）**
   - 优点：最完整，模型与配置也有 ORM 持久化
   - 缺点：过度工程化；model_artifacts 和 config_specs 持久化应作为独立 ADR；本阶段聚焦"同步"本身；冲突解决在 Git 原生能力已覆盖（git merge / git rebase）

**选择方案 B** 的关键理由：
- .vrm ZIP 包和 Git 仓库是两种不同的协作模式（离线分享 vs 在线同步），不应混淆
- 现有 ProjectStore 稳定且服务于 CAD 工程包，不应破坏
- 方案 B 聚焦"同步"本身，model_artifacts 和 config_specs 持久化作为后续 ADR（如 ADR-012/013）
- Git 原生冲突解决（merge/rebase）已足够，无需自建冲突解决逻辑
- 使用 `subprocess.run` 调用系统 git，不引入 gitpython 依赖，与现有 `GitCollector` 风格一致

## 后果

### 积极影响
- 用户可在项目级别同步数据集/模型/配置/工作流/快照/模板，支持团队协作
- 资源引用通过 URI + content_hash 实现内容寻址，同步时自动检测变更
- 同步历史记录可追溯（project_sync_records 表），便于审计与回滚
- 与阶段 5 SDK 对齐：`lomo project sync --push` / `lomo project sync --pull` 可在 CLI/SDK 层后续扩展
- 为阶段 6 p6-4（项目导入导出 .lomo 包）提供基础——Git 仓库可作为 .lomo 包的源

### 消极影响
- 新增 3 张数据库表，需配套 Alembic 迁移脚本
- 前端需新增项目同步 UI（本 ADR 仅交付契约/store，UI 在 p6-3/p6-4 阶段补齐）
- 依赖系统安装 git（需在文档中声明前置条件，服务层启动时检测 git 可用性）
- 大文件（模型 .pt / 数据集内容）不入 Git，仅记录 hash，用户需自行管理 content-addressable storage 的共享（如 S3 / NFS / 共享盘）

### 技术影响
- 数据库：新增 `project_repos` + `project_resource_refs` + `project_sync_records` 表，使用 JSONB(JSON) 双兼容模式存储 metadata
- Git 实现：使用 `subprocess.run(["git", ...])` 封装，不引入 gitpython 依赖；启动时检测 `git --version`，不可用时服务层降级（仅查询历史，不执行写操作）
- 线程安全：服务层使用 `threading.Lock` 保护 commit/push/pull 等写操作（同一项目串行化）
- 契约层：`ResourceRef` / `ProjectSyncManifest` / `SyncRecord` 是 `@dataclass(frozen=True)`，不可变，与现有契约风格一致
- 仓库存储位置：`<output_dir>/project_sync/<project_id>/`（默认 `python/output/project_sync/`）

### 业务影响
- 用户可创建可同步项目，邀请团队成员 clone/pull/push
- 项目同步状态可视化（clean/dirty/ahead/behind/conflict）
- 资源变更自动检测（hash 对比），commit 时自动更新清单
- 同步历史记录支持审计（谁在何时 push/pull 了什么）

## 实施计划

### 阶段 6 p6-2 交付物（本 ADR 范围）

| # | 文件 | 类型 | 说明 |
|---|------|------|------|
| 1 | `docs/adr/ADR-011-项目级Git同步.md` | 文档 | 本 ADR |
| 2 | `python/app/contracts/project_sync.py` | 契约 | ResourceType/SyncStrategy/SyncStatus 枚举 + ResourceRef/ProjectSyncManifest/SyncRecord dataclass |
| 3 | `python/app/contracts/__init__.py` | 修改 | 导出新契约 |
| 4 | `python/app/database/models/project_sync.py` | 数据库 | 3 张 SQLAlchemy 表 |
| 5 | `python/app/database/models/__init__.py` | 修改 | 导出新 ORM 模型 |
| 6 | `python/app/services/project_sync_service.py` | 服务 | Git 操作 + 资源引用管理 + 同步记录 |
| 7 | `python/app/api/v1/project_sync.py` | 路由 | 11 个 REST 端点 |
| 8 | `python/app/main.py` | 修改 | 注册路由 |
| 9 | `python/alembic/versions/f7a8b9c0d1e2_add_project_git_sync.py` | 迁移 | 创建 3 张表 + 索引 |
| 10 | `src/contracts/project_sync.ts` | 前端契约 | TS 类型 |
| 11 | `src/contracts/index.ts` | 修改 | 导出新契约 |
| 12 | `src/config/api.ts` | 修改 | 新增 API 路径 |
| 13 | `src/stores/projectSync.ts` | 前端 Store | Pinia store |

### 后续阶段

- **p6-3 模型/数据集卡片**：项目同步 UI 与模型/数据集卡片 UI 统一设计
- **p6-4 项目导入导出**：`.lomo` 包格式包含 Git 仓库快照
- **ADR-012（未来）**：model_artifacts ORM 表持久化（让模型也有版本表 + content_hash）
- **ADR-013（未来）**：config_specs ORM 表持久化（让配置也有版本表 + content_hash）
- **阶段 7 可解释性可视化**：项目同步历史接入 LTC 隐状态可视化时间线

## 相关文档

- [ADR-005-核心架构契约设计.md](./ADR-005-核心架构契约设计.md) —— 定义了 Dataset/Workflow/Snapshot 契约与 URI 体系
- [ADR-010-工作流模板市场.md](./ADR-010-工作流模板市场.md) —— p6-1 前置，模板作为项目同步资源之一
- [core-contracts-design.md](../development/core-contracts-design.md) 第 10 章阶段 6 路线图
- [project_store.py](../../python/app/projects/project_store.py) —— 现有 .vrm ZIP 工程包（本 ADR 不修改）
- [git_collector.py](../../python/app/observability/git_collector.py) —— 现有只读 Git 采集（本 ADR 复用为 dirty 检查）
- [dataset.py ORM](../../python/app/database/models/dataset.py) —— DatasetVersion.content_hash 字段来源

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-07-14 | 初始版本 | 灵境制造团队 |
