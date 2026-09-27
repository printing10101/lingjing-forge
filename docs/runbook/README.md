# 灵境制造系统运维手册

**版本**: 2.5.0
**最后更新**: 2026-07-10  
**维护团队**: 运维团队

> **本文档已对齐 v2.5.0 架构**：健康端点统一为 `/api/health/ping`，数据库路径统一为 `./data/app.db`。

---

## 目录

1. [系统概述](#系统概述)
2. [部署指南](#部署指南)
3. [配置管理](#配置管理)
4. [监控与告警](#监控与告警)
5. [备份与恢复](#备份与恢复)
6. [故障处理](#故障处理)
7. [性能优化](#性能优化)
8. [安全维护](#安全维护)
9. [常见问题](#常见问题)

---

## 系统概述

### 架构简介

灵境制造系统采用前后端分离架构：

- **前端**：Vue 3 + TypeScript + Vite
- **后端**：FastAPI + Python 3.10+
- **数据库**：SQLite（主数据库）+ Redis（缓存）
- **AI 引擎**：LNN（液态神经网络）+ PyTorch
- **部署**：Docker + Kubernetes（可选）

### 核心组件

1. **API 服务**：FastAPI 应用服务器
2. **LNN 引擎**：AI 推理服务
3. **数据库**：SQLite 数据库文件
4. **缓存服务**：Redis（可选）
5. **任务队列**：Celery + Redis（可选）
6. **监控系统**：Prometheus + Grafana

---

## 部署指南

### 环境要求

**最低配置**：
- CPU：4 核
- 内存：8 GB
- 存储：50 GB
- 操作系统：Linux (Ubuntu 20.04+) / Windows Server 2019+

**推荐配置**：
- CPU：8 核
- 内存：16 GB
- 存储：100 GB SSD
- 网络：100 Mbps+

### 部署方式

#### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆代码
git clone https://github.com/printing10101/Virtual-Realm-Manufacturing.git
cd Virtual-Realm-Manufacturing

# 2. 配置环境变量
cp .env.example .env
vim .env

# 3. 启动服务
docker-compose up -d

# 4. 验证服务
docker-compose ps
curl http://localhost:8765/api/health/ping
```

#### 方式二：直接部署

```bash
# 1. 安装依赖
# Python 依赖
cd python
pip install -r requirements.txt

# Node.js 依赖
cd ../frontend
pnpm install

# 2. 初始化数据库
cd ../python
alembic upgrade head

# 3. 启动后端服务
python start_server.py

# 4. 启动前端服务（开发环境）
cd ../frontend
pnpm dev

# 5. 构建前端（生产环境）
pnpm build
```

#### 方式三：Kubernetes 部署

```bash
# 1. 配置 Kubernetes
kubectl apply -f deploy/k8s/

# 2. 验证部署
kubectl get pods
kubectl get services

# 3. 查看日志
kubectl logs -f deployment/lingjing-api
```

### 部署验证

```bash
# 健康检查
curl http://localhost:8765/api/health/ping

# 预期响应
{
  "status": "healthy",
  "version": "2.5.0",
  "database": "connected",
  "lnn_engine": "ready"
}
```

---

## 配置管理

### 配置文件位置

> **P0-19 修复说明**：原版本文档错误引用了 `config/settings.yaml`、`config/database.yaml`、`config/lnn_workflow.yaml` 等不存在的配置文件。实际配置机制遵循 12-Factor App 原则，主配置通过 `.env` 环境变量 + `python/app/config.py` 中的 dataclass 配置类管理，LNN 工作流配置位于 `python/config/lnn_workflow.yaml`。

- **主配置模块**：`python/app/config.py`（dataclass 配置类，支持环境变量覆盖）
- **环境变量配置**：`.env`（由 `.env.example` / `.env.sqlite.example` / `.env.ai-cn.example` 提供模板）
- **LNN 工作流配置**：`python/config/lnn_workflow.yaml`
- **工艺规则配置**：`config/safety_rules.yaml`、`config/postprocessor_config.yaml`、`config/data_pipeline.yaml`

### 关键配置项

#### 数据库配置

数据库通过 `.env` 中的 `DATABASE_URL` 环境变量配置，默认使用 SQLite + WAL 模式：

```bash
# .env
DATABASE_URL=sqlite+aiosqlite:///./python/data/app.db
DB_PATH=./data/app.db
```

对应的 `python/app/config.py` 中的 `DatabaseConfig` 类：

```python
@dataclass
class DatabaseConfig:
    cad_db_path: str       # CAD 任务库路径
    model_library_path: str  # 模型库路径
    db_url: str            # 主数据库连接 URL（默认 sqlite+aiosqlite）
```

#### LNN 引擎配置

```yaml
# python/config/lnn_workflow.yaml
lnn:
  model_cache_size: 100
  inference_timeout: 30
  batch_size: 32
  device: cpu  # 或 cuda
  rule_confidence_threshold: 0.7
```

#### 服务配置

服务通过 `.env` 环境变量配置，由 `python/app/config.py` 中的 `ServerConfig` / `SecurityConfig` 读取：

```bash
# .env
SERVER_HOST=127.0.0.1
SERVER_PORT=8765
DEBUG=false

# 安全配置
LNN_JWT_SECRET=<your-strong-secret>
LNN_REGISTRATION_CODE=<optional-invite-code>
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
CORS_ALLOW_CREDENTIALS=true
ENVIRONMENT=production
```

### 环境变量

完整的 `.env` 模板参见 `.env.example`（或 `.env.sqlite.example` / `.env.ai-cn.example`）。关键变量：

```bash
# .env
SERVER_HOST=127.0.0.1
SERVER_PORT=8765
DATABASE_URL=sqlite+aiosqlite:///./python/data/app.db
LNN_JWT_SECRET=<your-strong-secret>
ALLOWED_ORIGINS=http://localhost:3000
ENVIRONMENT=production
LOG_LEVEL=INFO
LJ_ADMIN_INITIAL_PASSWORD=<initial-admin-password>
```

### 配置更新

```bash
# 重启服务以应用新配置
docker-compose restart

# 或
systemctl restart lingjing-api
```

---

## 监控与告警

### 监控指标

#### 系统指标

- CPU 使用率
- 内存使用率
- 磁盘使用率
- 网络 IO

#### 应用指标

- API 请求量
- 响应时间
- 错误率
- 并发连接数

#### 业务指标

- LNN 推理请求数
- 推理延迟
- 模型缓存命中率
- 任务执行成功率

### Prometheus 配置

```yaml
# deploy/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'lingjing-api'
    static_configs:
      - targets: ['localhost:8765']
    metrics_path: '/metrics'
```

### 告警规则

```yaml
# deploy/prometheus/alert_rules.yml
groups:
  - name: lingjing-alerts
    rules:
      - alert: HighCPUUsage
        expr: node_cpu_usage > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "CPU 使用率过高"
          
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "API 错误率过高"
          
      - alert: DatabaseConnectionFailed
        expr: database_connected == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "数据库连接失败"
```

### Grafana 仪表板

导入预定义仪表板：

1. 访问 Grafana：http://localhost:3000
2. 导入仪表板 ID：12345
3. 配置数据源为 Prometheus

### 日志管理

```bash
# 查看实时日志
docker-compose logs -f api

# 查看错误日志
tail -f logs/error.log

# 搜索特定错误
grep "ERROR" logs/app.log | tail -20
```

---

## 备份与恢复

### 自动备份

```bash
# 配置 crontab
crontab -e

# 添加备份任务（每天凌晨 2 点）
0 2 * * * /path/to/scripts/backup_postgres.sh
```

### 备份脚本

```bash
#!/bin/bash
# scripts/backup_postgres.sh
# 注意：项目实际提供的备份脚本为 backup_postgres.sh（针对 PostgreSQL 部署）。
# SQLite 部署可参考下方通用模板。

BACKUP_DIR="/backup/lingjing"
DATE=$(date +%Y%m%d_%H%M%S)
DB_PATH="./data/app.db"

# 创建备份目录
mkdir -p $BACKUP_DIR

# 备份数据库
sqlite3 $DB_PATH ".backup '$BACKUP_DIR/lingjing_$DATE.db'"

# 压缩备份
gzip $BACKUP_DIR/lingjing_$DATE.db

# 删除 30 天前的备份
find $BACKUP_DIR -name "lingjing_*.db.gz" -mtime +30 -delete

# 上传到 S3（可选）
aws s3 cp $BACKUP_DIR/lingjing_$DATE.db.gz s3://your-bucket/backups/
```

### 手动备份

```bash
# 备份数据库
sqlite3 data/app.db ".backup 'backup_$(date +%Y%m%d).db'"

# 备份配置文件
tar -czf config_backup_$(date +%Y%m%d).tar.gz config/

# 备份日志
tar -czf logs_backup_$(date +%Y%m%d).tar.gz logs/
```

### 恢复流程

```bash
# 1. 停止服务
docker-compose stop

# 2. 备份当前数据库（以防万一）
cp data/app.db data/app.db.backup

# 3. 恢复数据库
gunzip backup/lingjing_20240120_020000.db.gz
cp backup/lingjing_20240120_020000.db data/app.db

# 4. 启动服务
docker-compose start

# 5. 验证恢复
curl http://localhost:8765/api/health/ping
```

### 备份验证

```bash
# 验证备份完整性
sqlite3 backup/lingjing_20240120.db "PRAGMA integrity_check;"

# 预期输出：ok
```

---

## 故障处理

### 故障分类

| 级别 | 描述 | 响应时间 | 示例 |
|------|------|----------|------|
| P0 | 系统完全不可用 | 15 分钟 | 服务崩溃、数据库损坏 |
| P1 | 核心功能不可用 | 30 分钟 | API 无法访问、LNN 引擎故障 |
| P2 | 部分功能异常 | 2 小时 | 某些 API 报错、性能下降 |
| P3 | 轻微问题 | 24 小时 | 日志警告、UI 显示问题 |

### 常见故障及处理

#### 故障 1：服务无法启动

**症状**：
```bash
docker-compose up 失败
错误信息：Address already in use
```

**诊断**：
```bash
# 检查端口占用
netstat -tlnp | grep 8765

# 检查进程
ps aux | grep uvicorn
```

**解决**：
```bash
# 停止占用端口的进程
kill -9 <PID>

# 或修改配置使用其他端口
vim .env
# 修改 SERVER_PORT=8001

# 重启服务
docker-compose restart
```

#### 故障 2：数据库连接失败

**症状**：
```
Error: database is locked
Error: unable to open database file
```

**诊断**：
```bash
# 检查数据库文件权限
ls -lh data/app.db

# 检查磁盘空间
df -h

# 检查数据库完整性
sqlite3 data/app.db "PRAGMA integrity_check;"
```

**解决**：
```bash
# 修复权限
chmod 664 data/app.db

# 清理磁盘空间
rm -rf logs/*.log.gz

# 恢复数据库（从备份）
cp backup/latest.db data/app.db
```

#### 故障 3：LNN 引擎推理超时

**症状**：
```
Error: LNN inference timeout
响应时间 > 30 秒
```

**诊断**：
```bash
# 检查 LNN 引擎状态
curl http://localhost:8765/api/health/ping

# 检查模型加载情况
curl http://localhost:8765/api/v1/lnn/models

# 查看 LNN 日志
tail -f logs/lnn.log
```

**解决**：
```bash
# 重启 LNN 引擎
docker-compose restart lnn-engine

# 清理模型缓存
curl -X POST http://localhost:8765/api/v1/lnn/cache/clear

# 调整超时配置
vim python/config/lnn_workflow.yaml
# 增加 inference_timeout: 60
```

#### 故障 4：内存溢出

**症状**：
```
Error: Out of memory
服务自动重启
```

**诊断**：
```bash
# 检查内存使用
free -h

# 检查进程内存占用
ps aux --sort=-%mem | head -10
```

**解决**：
```bash
# 增加系统内存（云环境）
# 或优化配置

# 减少 workers 数量
vim .env
# SERVER_WORKERS=2（如使用 docker-compose 部署）

# 减少模型缓存大小
vim python/config/lnn_workflow.yaml
# model_cache_size: 50

# 重启服务
docker-compose restart
```

#### 故障 5：API 响应缓慢

**症状**：
```
响应时间 > 5 秒
用户投诉系统卡顿
```

**诊断**：
```bash
# 检查慢查询日志
tail -f logs/slow_queries.log

# 检查数据库性能
sqlite3 data/app.db "EXPLAIN QUERY PLAN SELECT * FROM tasks;"

# 检查系统负载
top
```

**解决**：
```bash
# 优化数据库索引
sqlite3 data/app.db "CREATE INDEX idx_tasks_status ON tasks(status);"

# 清理历史数据
sqlite3 data/app.db "DELETE FROM logs WHERE created_at < date('now', '-90 days');"

# 启用缓存
vim .env
# CACHE_ENABLED=true
```

### 故障上报流程

```
1. 发现故障
   ↓
2. 初步诊断（5 分钟）
   ↓
3. 确定故障级别
   ↓
4. 通知相关人员
   - P0/P1：立即通知运维负责人
   - P2：通知运维团队
   - P3：记录到问题跟踪系统
   ↓
5. 执行故障处理
   ↓
6. 验证恢复
   ↓
7. 编写故障报告
```

### 故障报告模板

```markdown
# 故障报告

**故障编号**: INC-2024-001
**故障时间**: 2024-01-20 14:30 - 15:00
**故障级别**: P1
**影响范围**: 全部用户

## 故障描述
{详细描述故障现象}

## 根本原因
{分析故障根本原因}

## 处理过程
{记录处理步骤和时间线}

## 改进措施
{提出防止再次发生的措施}

## 经验教训
{总结经验教训}
```

---

## 性能优化

### 数据库优化

```bash
# 分析慢查询
sqlite3 data/app.db "EXPLAIN QUERY PLAN SELECT * FROM tasks WHERE status='running';"

# 创建索引
sqlite3 data/app.db "CREATE INDEX idx_tasks_status ON tasks(status);"
sqlite3 data/app.db "CREATE INDEX idx_tasks_created ON tasks(created_at);"

# 清理碎片
sqlite3 data/app.db "VACUUM;"

# 调整 PRAGMA
sqlite3 data/app.db "PRAGMA cache_size = 10000;"
sqlite3 data/app.db "PRAGMA journal_mode = WAL;"
```

### 应用优化

通过 `.env` 环境变量调整应用层配置（由 `python/app/config.py` 读取）：

```bash
# .env
SERVER_WORKERS=4  # 根据 CPU 核心数调整
CACHE_ENABLED=true
CACHE_BACKEND=redis
CACHE_TTL=3600
COMPRESSION_ENABLED=true
COMPRESSION_MIN_SIZE=1024
```

### LNN 引擎优化

```yaml
# python/config/lnn_workflow.yaml
lnn:
  batch_size: 32  # 增大批处理量
  model_cache_size: 100  # 增加缓存大小
  inference_timeout: 30
  device: cuda  # 使用 GPU（如有）
```

### 监控性能

```bash
# 查看性能指标
curl http://localhost:8765/metrics

# 使用 Apache Bench 压测
ab -n 1000 -c 10 http://localhost:8765/api/health/ping
```

---

## 安全维护

### 定期更新

```bash
# 更新系统包
sudo apt update && sudo apt upgrade -y

# 更新 Python 依赖
cd python
pip list --outdated
pip install -U -r requirements.txt

# 更新 Node.js 依赖
cd frontend
pnpm update
```

### 安全扫描

```bash
# Python 安全扫描
pip-audit

# Node.js 安全扫描
cd frontend
pnpm audit

# 修复漏洞
pip-audit --fix
pnpm audit fix
```

### 日志审计

```bash
# 检查异常登录
grep "Failed login" logs/auth.log | tail -20

# 检查异常操作
grep "ERROR" logs/app.log | tail -20

# 检查安全事件
grep "security" logs/app.log | tail -20
```

### 备份安全

```bash
# 加密备份
gpg -c backup/lingjing_20240120.db

# 设置备份文件权限
chmod 600 backup/*.db.gpg

# 定期清理旧备份
find backup/ -name "*.db.gpg" -mtime +90 -delete
```

---

## 常见问题

### Q1: 如何查看系统版本？

```bash
curl http://localhost:8765/version
```

### Q2: 如何重置管理员密码？

项目未提供独立的 CLI 脚本。管理员密码通过环境变量 `LJ_ADMIN_INITIAL_PASSWORD` 在初始化时设置，初始密码会被写入日志目录下的 `admin_initial_password.txt`（仅首次创建时）。

```bash
# 方法 1：通过环境变量重新初始化（需先删除现有 admin 用户记录）
# 适用于开发/测试环境
cd python
# 删除现有 admin 用户（SQLite）
sqlite3 data/app.db "DELETE FROM users WHERE username='admin';"
# 设置新密码并重启
export LJ_ADMIN_INITIAL_PASSWORD=<new-strong-password>
python start_server.py
# 首次启动后，admin 账号会自动重建（must_change_password=true）

# 方法 2：直接修改密码哈希（生产环境，需提前生成哈希）
# 使用 Python REPL 生成 bcrypt 哈希
python -c "from app.auth.security import hash_password; print(hash_password('<new-password>'))"
# 然后更新数据库
sqlite3 data/app.db "UPDATE users SET password_hash='<hash>', must_change_password=1 WHERE username='admin';"
```

### Q3: 如何清理历史数据？

```bash
# 清理 90 天前的日志
sqlite3 data/app.db "DELETE FROM logs WHERE created_at < date('now', '-90 days');"

# 清理已完成的任务
sqlite3 data/app.db "DELETE FROM tasks WHERE status='completed' AND completed_at < date('now', '-30 days');"
```

### Q4: 如何扩容？

**垂直扩容**：增加服务器资源（CPU、内存）

**水平扩容**：
```bash
# 增加 workers 数量
vim .env
# SERVER_WORKERS=8

# 或使用负载均衡
# 部署多个实例，使用 Nginx 负载均衡（参考 deploy/nginx/nginx.conf）
```

### Q5: 如何迁移数据？

```bash
# 导出数据库
sqlite3 data/app.db ".dump" > backup.sql

# 在新服务器导入
sqlite3 new_app.db < backup.sql
```

---

## 联系支持

- **运维团队**：ops@your-company.com
- **技术支持**：support@your-company.com
- **紧急联系**：+86-xxx-xxxx-xxxx

---

## 附录

### 常用命令速查

```bash
# 服务管理
docker-compose up -d          # 启动服务
docker-compose down           # 停止服务
docker-compose restart        # 重启服务
docker-compose logs -f        # 查看日志

# 数据库操作
sqlite3 data/app.db      # 进入数据库
.backup backup.db             # 备份数据库
.restore backup.db            # 恢复数据库

# 性能监控
top                           # 系统资源
df -h                         # 磁盘空间
free -h                       # 内存使用
netstat -tlnp                 # 端口占用
```

### 相关文档

- [部署指南](#部署指南)
- [备份恢复](#备份与恢复)
- [故障处理手册](./troubleshooting.md)
- [安全维护](#安全维护)
