"""项目级 Git 同步服务包.

对应 ADR-011 阶段 6 p6-2：项目级 Git 同步业务逻辑。

职责：
    1. Git 操作封装：init / status / commit / push / pull / clone（subprocess.run）
    2. 资源引用管理：add_ref / remove_ref / update_ref_hash / list_refs
    3. 同步记录管理：record_sync_operation（每次 Git 写操作生成审计记录）
    4. 项目仓库 CRUD：create_project / get_project / list_projects / delete_project
    5. content_hash 计算：对 model/workflow/config/snapshot/template 资源计算 sha256
    6. .lomo-project.yaml 清单文件读写
    7. git 可用性检测：启动时 git --version，不可用时降级

并发安全：
    - Git 写操作通过 ``_project_locks[project_id]`` 串行化（同一项目内的
      commit/push/pull 互斥，不同项目可并发）
    - 数据库操作通过 SQLAlchemy 事务保证原子性，写操作显式 commit()

降级策略：
    - git 不可用时：init/commit/push/pull/clone 抛 ``GitUnavailableError``，
      查询类操作（list_projects / get_project / list_sync_records）仍可正常执行
    - 仓库目录不存在时：get_project_status 抛 ``ProjectNotFoundError``

仓库存储位置：``<output_dir>/project_sync/<project_id>/``

模块结构：
    - ``_exceptions``：自定义异常 + _GitResult
    - ``_git_ops``：git 命令封装与状态查询（_GitOpsMixin）
    - ``_manifest``：清单文件读写（_ManifestMixin）
    - ``_hashing``：content_hash 计算（_HashingMixin）
    - ``_sync_records``：同步记录管理（_SyncRecordsMixin）
    - ``_project_crud``：项目 CRUD（_ProjectCrudMixin）
    - ``_resource_refs``：资源引用管理（_ResourceRefMixin）
    - ``_commit``：commit 流程（_CommitMixin）
    - ``_remote``：push / pull（_RemoteMixin）
    - ``_clone``：clone（_CloneMixin）
    - ``service``：主类 ProjectSyncService（组合所有 Mixin）
"""

from __future__ import annotations

from app.dependencies import get_project_sync_service
