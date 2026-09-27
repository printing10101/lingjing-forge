# 灵境制造 部署故障排查手册

本手册覆盖常见部署问题的症状、根因、排查步骤与修复方案。按部署模式分章组织。

---

## 一、SQLite 单机模式

### 1.1 启动报错 `unable to open database file`

**症状**：`python start_server.py` 启动后日志出现 `sqlite3.OperationalError: unable to open database file`。

**根因**：DB_URL 指向的目录不存在或无写权限。

**排查**：
```bash
# 查看 DB_URL 配置
echo $DB_URL
# 或在 Python 中
python -c "from app.config import config; print(config.database.db_url)"
```

**修复**：
- 确保父目录存在：`mkdir -p <db_dir>`
- Windows 下避免路径含中文或空格
- 检查目录写权限

### 1.2 启动卡在 `Calling init_db() ...`

**根因**：多 Base（TaskBase/RuleBase/MachiningRecordBase/KnowledgeGraphBase）建表慢或 alembic 迁移阻塞。

**排查**：
- 查看后端日志最后几行是否停留在 `[startup] Calling init_db() ...`
- 设置 `LNN_ALEMBIC_ENABLED=false` 临时跳过 alembic 迁移验证是否为此原因

**修复**：
- 首次启动允许较长时间（30s 内正常）
- 若超 60s 仍未完成，检查磁盘 IO 与 SQLite 文件是否被其他进程锁定（Windows 常见）
- 设置 `LNN_ALEMBIC_ENABLED=false` 临时绕过（仅排查用，不建议长期关闭）

---

## 二、Tauri 桌面 sidecar 模式

### 2.1 splashscreen 卡在"正在启动后端服务"超过 10 秒

**症状**：启动 Tauri 桌面应用后，splashscreen 长时间不消失。

**根因**：
- Python sidecar 启动失败（依赖缺失/端口占用）
- sidecar.json 状态文件路径不一致，Rust 端读不到 failed 状态

**排查**：
1. 查看 sidecar 日志：`%APPDATA%\com.lingjing.v4\logs\python.stdout.log`
2. 检查 sidecar.json：`%APPDATA%\com.lingjing.v4\logs\sidecar.json`
   - `status: "failed"` 表示 Python 启动失败，查看 `error` 字段
   - `status: "running"` 但 Rust 仍等待 → 路径不一致问题
3. 检查 8765 端口是否被占用：`netstat -ano | findstr :8765`

**修复**：
- 依赖缺失：`pip install -r requirements.txt`
- 端口占用：修改 `.env` 中 `LNN_PORT` 或释放端口
- 路径不一致：确认 `LNN_LOG_DIR` 已设置，三方（Python main.py / sidecar_main.py / Rust sidecar.rs）统一读取 `$LNN_LOG_DIR/sidecar.json`

### 2.2 点击"跳过等待"后前端报 401

**根因**：后端未真正启动完成，JWT 密钥未初始化。

**修复**：等待后端真正就绪（日志出现 `Application startup complete`）后再操作。

### 2.3 关闭应用时 sidecar 进程残留

**症状**：关闭 Tauri 后任务管理器仍能看到 python.exe。

**根因**：Rust 端 stop() 的 HTTP 通知未到达，且 kill() 兜底未执行。

**排查**：
- 检查 `/api/v1/admin/shutdown` 端点是否注册（仅 `LNN_IDLE_AUTO_SHUTDOWN=false` 时注册）
- 查看 Rust 日志 `[shutdown]` 相关条目

**修复**：
- 桌面 sidecar 模式必须设置 `LNN_IDLE_AUTO_SHUTDOWN=false`
- 手动清理残留进程：`taskkill /f /im python.exe`

---

## 三、Docker 模式

### 3.1 `docker-compose up` 报密码缺失

**症状**：`ERROR: missing required env POSTGRES_PASSWORD`。

**修复**：
```bash
cp .env.example .env
# 编辑 .env，填入所有敏感字段
docker-compose --env-file .env up -d
```

### 3.2 nginx 启动报证书不存在

**症状**：nginx 容器启动失败，日志 `cannot load certificate`。

**根因**：`deploy/nginx/certs/` 目录为空。

**修复**：
```bash
# 开发环境：生成自签名证书
bash deploy/nginx/generate_dev_cert.sh
# 生产环境：放入正式证书到 deploy/nginx/certs/fullchain.pem 和 privkey.pem
```

### 3.3 HEALTHCHECK 一直 unhealthy

**排查**：
```bash
docker exec <container> curl -f http://localhost:8765/api/health/ping
```
- 返回非 200 → 后端启动失败，查看容器日志
- 返回 200 但 HEALTHCHECK 仍 unhealthy → 检查 curl 是否在镜像中（Dockerfile 已安装）

---

## 四、K8s 模式

### 4.1 Pod `CrashLoopBackOff`

**排查**：
```bash
kubectl logs <pod> --previous
kubectl describe pod <pod>
```

**常见根因**：
- `readOnlyRootFilesystem: true` 但 SQLite 目录未挂载可写卷 → 确认 `data-volume` 已挂载到 `/app/python/data`
- env 缺失 → 确认 ConfigMap `lnn-config` 和 Secret `lnn-secrets` 已创建
- 私有镜像拉取失败 → 配置 `imagePullSecrets`

### 4.2 探针 404 导致 Pod 重启

**症状**：`liveness probe failed: HTTP probe failed with statuscode: 404`。

**根因**：探针路径错误（曾误用 `/api/v1/health`）。

**修复**：探针路径必须为 `/api/health/ping`（simple_health_router 注册路径）。

### 4.3 创建 Secret 与 ConfigMap

```bash
# ConfigMap（非敏感）
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: lnn-config
data:
  app-env: "production"
  tdengine-host: "lnn-tdengine"
  tdengine-port: "6030"
  tdengine-database: "lingjing"
EOF

# Secret（敏感）
kubectl create secret generic lnn-secrets \
  --from-literal=redis-url=redis://default:password@lnn-redis:6379/0 \
  --from-literal=db-url=postgresql+asyncpg://postgres:password@lnn-postgres:5432/lingjing \
  --from-literal=jwt-secret=$(openssl rand -hex 32) \
  --from-literal=tdengine-user=root \
  --from-literal=tdengine-password=taosdata
```

### 4.4 启用私有镜像 imagePullSecrets

```bash
kubectl create secret docker-registry regcred \
  --docker-server=<registry> \
  --docker-username=<user> \
  --docker-password=<pass> \
  --docker-email=<email>
```
然后编辑 `deploy/k8s/deployment.yml`，取消 `imagePullSecrets` 注释。

---

## 五、跨模式通用问题

### 5.1 前端跨域请求 401（CORS 预检失败）

**症状**：浏览器控制台报 `CORS error` 或 `401 Unauthorized` on OPTIONS。

**根因**：CORS 中间件在 UnifiedAuth 内层，OPTIONS 预检被鉴权拦截。

**修复**：确认 main.py 中间件注册顺序（P2-3 修复后）：
- 注册顺序（内→外）：IdleAutoShutdown → UnifiedAuth → Metrics → CORS → SecurityHeaders → RequestId
- 执行顺序（外→内）：RequestId → SecurityHeaders → CORS → Metrics → UnifiedAuth → IdleAutoShutdown

### 5.2 JWT 密钥不一致导致登录后立即 401

**根因**：多入口（main.py / sidecar_main.py / start_server.py）JWT 密钥生成方式不一致。

**修复**：统一通过 `LNN_JWT_SECRET` 环境变量注入，或确认所有入口都使用 `config.security.jwt_secret_key`。

### 5.3 离线包准备失败

**症状**：`prepare_offline.sh` 执行报错。

**排查**：
- pip 下载失败：尝试设置 `PIP_INDEX_URL` 切换源，或设置 `PIP_PLATFORM=win_amd64` 跨平台下载
- Docker 镜像拉取失败：检查网络或手动准备对应镜像 tar 包

### 5.4 备份脚本失败

**症状**：`backup_postgres.sh` 退出码非零。

**排查**：
- `POSTGRES_PASSWORD 环境变量未设置` → 补齐环境变量
- `pg_dump: connection refused` → 检查 POSTGRES_HOST/PORT
- 管道失败但脚本退出码 0 → 已通过 `pipefail` 修复，确认脚本版本含 `set -o pipefail`

---

## 六、关键环境变量速查

| 变量 | 作用 | 默认值 | 适用场景 |
|------|------|--------|----------|
| `LNN_IDLE_AUTO_SHUTDOWN` | 空闲自动关机 | `true` | 桌面 sidecar 设 `false` |
| `LNN_JWT_SECRET` | JWT 密钥 | 自动生成 | 生产环境必须显式设置 |
| `LNN_LOG_DIR` | 日志目录 | gstack 同级 | 决定 sidecar.json 路径 |
| `LNN_GSTACK_DIR` | 工作目录 | 用户数据目录 | K8s 下需指向可写卷 |
| `LNN_ENVIRONMENT` | 环境标识 | `development` | `production` 时关闭 docs 端点 |
| `LNN_ALEMBIC_ENABLED` | 是否执行迁移 | `true` | 排查时可临时设 `false` |
| `LNN_SKIP_OLLAMA` | 跳过 Ollama | 按 hardware 档位 | 轻量模式设 `true` |

---

## 七、日志位置

| 模式 | 位置 |
|------|------|
| 桌面 sidecar（Windows） | `%APPDATA%\com.lingjing.v4\logs\` |
| 桌面 sidecar（macOS） | `~/Library/Logs/com.lingjing.v4/` |
| 桌面 sidecar（Linux） | `~/.local/share/com.lingjing.v4/logs/` |
| Docker | `docker logs <container>` |
| K8s | `kubectl logs <pod>` |
| 开发模式 | 控制台 + `<gstack>/logs/` |

---

## 八、版本与状态文件

- **sidecar.json**：sidecar 状态文件，路径为 `$LNN_LOG_DIR/sidecar.json`（未设置时回退到 `$LNN_GSTACK_DIR/sidecar.json`）
  - `status` 字段：`starting` / `running` / `failed` / `stopped`
  - Rust 端通过此文件快速感知后端状态，避免 45s 超时
- **VERSION**：版本号文件，发布时需同步更新 `tauri.conf.json`、`main.py`、K8s deployment.yml 镜像标签
