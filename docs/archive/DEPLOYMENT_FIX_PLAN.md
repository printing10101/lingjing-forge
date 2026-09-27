# 灵境制造（上线版）部署可用性修复计划

> 文档版本：v1.0
> 生成时间：2026-07-09
> 适用项目：灵境制造（上线版） v2.5.0
> 修复目标：消除前两轮排查发现的 36 项部署可用性问题，确保 SQLite 单机模式与 Docker/K8s 部署模式均可独立启动并完成登录闭环。

---

## 一、问题清单总览

| 等级 | 数量 | 含义 |
|------|------|------|
| P0   | 15   | 阻断启动或登录闭环，必须修复 |
| P1   | 11   | 影响部署稳定性与运维体验 |
| P2   | 10   | 工程规范类问题，统一收口 |

修复完成后将执行全量复查，对照本表逐项核验。

---

## 二、P0 问题修复方案

### P0-1 根 requirements.txt 缺失关键依赖
- **现象**：根 `requirements.txt` 缺 `aiosqlite`、`alembic`、`asyncpg`；`python/requirements.txt` 缺 `alembic`、`asyncpg`。
- **影响**：SQLite 异步驱动缺失，`sqlite+aiosqlite://` URL 直接报 `NoSuchModuleError`。
- **修复**：
  - 根 `requirements.txt` 补齐 `aiosqlite>=0.19.0`、`alembic>=1.13.0`、`asyncpg>=0.29.0`、`greenlet>=3.0.0`。
  - `python/requirements.txt` 补齐 `alembic>=1.13.0`、`asyncpg>=0.29.0`、`greenlet>=3.0.0`。
- **目标文件**：`requirements.txt`、`python/requirements.txt`

### P0-2 init_db() 只创建 1 套 Base
- **现象**：`python/app/database/models/training_task.py::init_db()` 仅 `Base.metadata.create_all`，而项目实际有 4 套 Base（TaskBase / RuleBase / MachiningRecordBase / KnowledgeGraphBase）。
- **影响**：用户表、角色表、规则表、知识图谱表均不创建，登录失败。
- **修复**：在 `init_db()` 内统一导入并 `create_all` 全部 4 套 Base 的 metadata。
- **目标文件**：`python/app/database/models/training_task.py`

### P0-3 启动钩子未执行 alembic 迁移
- **现象**：`python/app/main.py::startup_event` 仅调用 `init_db()`，无 `alembic upgrade head`。
- **影响**：版本演进后 schema 漂移。
- **修复**：在 `startup_event` 中追加 `await _run_alembic_upgrade()`，失败仅告警不阻断启动。
- **目标文件**：`python/app/main.py`

### P0-4 默认 admin 用户未创建
- **现象**：`_seed_rbac` 只种子角色与权限，无用户，导致首次登录无账号可用。
- **修复**：在 `_seed_rbac` 末尾种子默认 admin 用户（用户名 `admin`，密码取自 `LNN_DEFAULT_ADMIN_PASSWORD`，缺省 `CHANGE_ME_admin_2026` 并在日志中打印强提示要求立即修改）。
- **目标文件**：`python/app/database/models/training_task.py`

### P0-5 start_server.py 无条件覆盖 JWT 密钥
- **现象**：`python/start_server.py::main()` 第一行 `os.environ["LNN_JWT_SECRET"] = secrets.token_urlsafe(32)`，覆盖 .env 中已配置的密钥。
- **修复**：改为条件设置——`if not os.environ.get("LNN_JWT_SECRET"): ...`，并打印是否使用环境变量或新生成。
- **目标文件**：`python/start_server.py`、`python/start_backend.ps1`

### P0-6 start_server.bat 缺 LNN_JWT_SECRET
- **现象**：`python/start_server.bat` 完全未设置 JWT 密钥，依赖应用内部生成，跨重启失效。
- **修复**：在 bat 中追加 `if "%LNN_JWT_SECRET%"==""` 判断，缺失时生成临时密钥并打印。
- **目标文件**：`python/start_server.bat`

### P0-7 os._exit(0) 绕过 FastAPI shutdown
- **现象**：`python/app/sidecar/sidecar_lifecycle.py::_perform_graceful_shutdown` 在 `_cleanup_all_resources()` 后 `os._exit(0)` 硬退出，绕过 `@app.on_event("shutdown")` 注册的清理逻辑。
- **修复**：替换为 `await asyncio.from_thread(sys.exit, 0)` 不适用，统一改为：清理完成后调用 `os._exit(0)` 改为 `raise SystemExit(0)`，并在 `sidecar_main.py` 入口包一层 try/except SystemExit。
- **目标文件**：`python/app/sidecar/sidecar_lifecycle.py`

### P0-8 agent_state 路由未注册
- **现象**：`python/app/main.py` 52 处 include_router 缺 `agent_state.router`。
- **影响**：前端调用 `/api/agent/state/*` 404。
- **修复**：补齐 `from app.api.v1 import agent_state` 与 `app.include_router(agent_state.router, prefix="/api/agent/state", tags=["agent-state"])`。
- **目标文件**：`python/app/main.py`

### P0-9 Rust 默认端口 8000 ≠ 8765
- **现象**：`src-tauri/src/lib.rs` 中 `DEFAULT_BACKEND_PORT: u16 = 8000`，与 Python `config.server.port = 8765` 不一致。
- **修复**：改为 `8765`。
- **目标文件**：`src-tauri/src/lib.rs`

### P0-10 splashscreen 超时 12s ≠ 10s
- **现象**：`splashscreen.html` 中 `setTimeout(forceCloseSplashscreen, 12000)`，与项目约定（lib.rs 与文档均为 10s）不一致。
- **修复**：统一为 10000ms；跳过按钮显示时间从 8s 调整为 6s。
- **目标文件**：`splashscreen.html`

### P0-11 sidecar.json 路径三方不一致
- **现象**：`sidecar_lifecycle.py` 写入路径取自 `state_file` 参数；`sidecar_main.py` 默认 `Path.cwd()/"sidecar.json"`；`src-tauri/src/sidecar.rs` 读取 `log_dir/sidecar.json`。三处不一致。
- **修复**：
  - `sidecar_main.py` 默认 state_file 改为 `os.environ.get("LNN_LOG_DIR", ".") + "/sidecar.json"`，与 Rust 侧 `log_dir/sidecar.json` 对齐。
  - `src-tauri/src/sidecar.rs::start()` 显式传 `--state-file` 参数指向 `log_dir/sidecar.json`。
- **目标文件**：`python/sidecar_main.py`、`src-tauri/src/sidecar.rs`

### P0-12 gstack_dir 相对路径漂移
- **现象**：`python/sidecar_main.py` 在 PyInstaller 模式下 `os.chdir(bundle_dir)`，导致相对路径 `.lingjing/.gstack` 漂移到 `sys._MEIPASS`。
- **修复**：在 chdir 之前解析 `gstack_dir` 为绝对路径；或显式设置 `LNN_GSTACK_DIR` 环境变量为 `<user_data_dir>/.lingjing/.gstack`。
- **目标文件**：`python/sidecar_main.py`

### P0-13 Dockerfile 镜像源与基础镜像硬绑定
- **现象**：`Dockerfile` 基础镜像固定华为云 SWR，pip 源固定阿里云，海外构建失败。
- **修复**：引入 `ARG IMAGE_REGISTRY`、`ARG PIP_INDEX_URL`，默认值保持国内镜像，可通过 build-arg 切换。
- **目标文件**：`Dockerfile`

### P0-14 .env.example 必填密码默认值缺失
- **现象**：`POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`GF_SECURITY_ADMIN_PASSWORD`、`TDENGINE_PASSWORD` 全部为空，docker-compose 中 `:?` 强制报错阻断启动。
- **修复**：填入开发占位默认值（生产环境提示必改），如 `CHANGE_ME_STRONG_16` 等。
- **目标文件**：`.env.example`

### P0-15 nginx certs 目录不存在
- **现象**：`docker-compose.yml` 挂载 `./deploy/nginx/certs:/etc/nginx/certs:ro`，但目录不存在导致 docker-compose 启动失败。
- **修复**：创建 `deploy/nginx/certs/.gitkeep` 占位，并在 README 中提示用户放置证书。
- **目标文件**：`deploy/nginx/certs/.gitkeep`

---

## 三、P1 问题修复方案

### P1-1 IdleAutoShutdownMiddleware 桌面场景默认禁用
- **现象**：`idle_timeout: int = 1800`，桌面场景下用户离开 30 分钟后后端自动关闭。
- **修复**：引入 `LNN_IDLE_SHUTDOWN_ENABLED` 环境变量，桌面 sidecar 模式默认 `false`，Docker/K8s 默认 `true`。
- **目标文件**：`python/app/sidecar/sidecar_lifecycle.py`、`python/app/main.py`

### P1-2 get_db() 对 GET 请求也 commit
- **现象**：`python/app/database/connection.py::get_db()` 无差别 commit，GET 请求也会触发事务提交。
- **修复**：仅在有写入操作（POST/PUT/PATCH/DELETE）或显式 `session.modified` 时 commit；GET 请求仅 close。
- **目标文件**：`python/app/database/connection.py`

### P1-3 SQLite async engine 未配置 PRAGMA
- **现象**：SQLite 默认未启用 WAL 与外键约束。
- **修复**：在 engine 创建处通过 `connect_args={"pragma": [...]}` 或事件监听器配置 `PRAGMA journal_mode=WAL`、`PRAGMA foreign_keys=ON`、`PRAGMA synchronous=NORMAL`。
- **目标文件**：`python/app/database/connection.py`

### P1-4 sidecar.stop() 非优雅关闭
- **现象**：`src-tauri/src/sidecar.rs::stop()` 使用 `child.kill()` 直接 SIGKILL，未尝试 SIGTERM 优雅关闭。
- **修复**：先发 SIGTERM（Windows 下用 `taskkill /PID` 无 /F），等待 5s 后未退出再 kill。
- **目标文件**：`src-tauri/src/sidecar.rs`

### P1-5 .env.example TDengine 主机名错误
- **现象**：`TDENGINE_URL=taos://root:CHANGE_ME@tdengine:6030`，但 docker-compose 中服务名为 `lnn-tdengine`。
- **修复**：统一为 `lnn-tdengine`。
- **目标文件**：`.env.example`

### P1-6 prepare_offline.sh 引用不存在的 docker-compose-cn.yml
- **现象**：line 97 `cp docker-compose-cn.yml` 文件不存在。
- **修复**：改为条件复制 `cp ... 2>/dev/null || true`，并打印跳过提示。
- **目标文件**：`deploy/offline/prepare_offline.sh`

### P1-7 prepare_offline.sh 镜像版本不符
- **现象**：line 79 `redis:7-alpine postgres:16-alpine`，但 docker-compose 实际版本可能不同。
- **修复**：从 docker-compose.yml 解析实际版本，或显式声明与 compose 一致的版本。
- **目标文件**：`deploy/offline/prepare_offline.sh`

### P1-8 pip download 缺 --platform
- **现象**：`pip download` 未指定 `--platform`、`--only-binary=:all:`，跨平台离线包可能下载错误架构 wheel。
- **修复**：从环境变量 `TARGET_PLATFORM` 读取目标平台（如 `manylinux2014_x86_64`），未设置时警告并使用当前平台。
- **目标文件**：`deploy/offline/prepare_offline.sh`

### P1-9 K8s readOnlyRootFilesystem 缺可写卷
- **现象**：`deploy/k8s/deployment.yml` 容器 `readOnlyRootFilesystem: true`，但 SQLite DB 路径 `/app/python/data` 无可写卷。
- **修复**：挂载 `data-volume` emptyDir 到 `/app/python/data`。
- **目标文件**：`deploy/k8s/deployment.yml`

### P1-10 K8s env 缺失
- **现象**：env 只有 REDIS_URL、DB_URL，缺 TDENGINE_*、LNN_JWT_SECRET、LNN_GSTACK_DIR 等。
- **修复**：补全 envFrom configMapRef 与 secretKeyRef。
- **目标文件**：`deploy/k8s/deployment.yml`

### P1-11 K8s 缺 imagePullSecrets
- **现象**：私有镜像 `lnn-api:2.5.0` 无 imagePullSecrets。
- **修复**：添加注释 `# imagePullSecrets:` 示例与生产启用说明。
- **目标文件**：`deploy/k8s/deployment.yml`

---

## 四、P2 问题修复方案（批量收口）

| 编号 | 问题 | 修复 |
|------|------|------|
| P2-1 | main.py 生产环境暴露 docs_url | 通过 `LNN_ENVIRONMENT` 条件切换 docs_url |
| P2-2 | main.py `reload=True` 写死 | 改为 `config.server.debug` |
| P2-3 | 中间件注册顺序不当 | 调整为 CORS → 限流 → 鉴权 → 业务 |
| P2-4 | Dockerfile 缺 healthcheck | 添加 `HEALTHCHECK CMD curl -f /api/health/ping` |
| P2-5 | backup_postgres.sh 路径错误 | 修正 PGPASSWORD 与 dump 路径 |
| P2-6 | troubleshooting.md 缺失 | 创建 deploy/docs/troubleshooting.md |
| P2-7 | pyproject.toml ruff per-file-ignores 引用不存在的文件 | 删除失效规则 |
| P2-8 | README badge 链接错误 | 修正或移除 |
| P2-9 | README 测试命令路径错误 | 修正为 `python -m pytest python/tests` |
| P2-10 | tauri.conf.json CSP 缺 ipc: | 补齐 `ipc:` 与 `http://ipc.localhost` |

---

## 五、修复执行顺序

1. P0-1 → P0-2 → P0-3 → P0-4（依赖链：依赖 → 建表 → 迁移 → 种子用户）
2. P0-5/6 → P0-7（JWT 与 shutdown）
3. P0-8 → P0-9/10 → P0-11/12（路由与端口、路径）
4. P0-13/14/15（Docker）
5. P1-1/2/3（DB 与中间件）
6. P1-4 → P1-5/6/7/8 → P1-9/10/11
7. P2 批量修复
8. 最终复查

---

## 六、最终复查检查清单

- [ ] SQLite 单机模式：`python start_server.py` 能启动并完成 `POST /api/v1/auth/login`
- [ ] Docker 模式：`docker-compose --env-file .env.example up` 不报密码缺失
- [ ] K8s 模式：`kubectl apply -f deploy/k8s/` Pod 启动探针成功
- [ ] Tauri 桌面：`cargo tauri dev` 后端 sidecar 启动、splashscreen 10s 内就绪
- [ ] 默认 admin 用户可登录
- [ ] 全部 P0 修复点回归通过
- [ ] 全部 P1 修复点回归通过
- [ ] P2 修复点抽查通过
