# 故障处理手册

**版本**: 1.0.0  
**最后更新**: 2026-07-10  
**适用范围**: 灵境制造系统所有运维人员

---

## 目录

1. [故障处理流程](#故障处理流程)
2. [故障分级标准](#故障分级标准)
3. [常见故障处理](#常见故障处理)
4. [故障诊断工具](#故障诊断工具)
5. [故障升级机制](#故障升级机制)
6. [故障报告模板](#故障报告模板)

---

## 故障处理流程

### 标准处理流程

```
1. 故障发现
   ↓
2. 初步诊断（5 分钟内）
   ↓
3. 故障定级
   ↓
4. 通知相关人员
   ↓
5. 故障处理
   ↓
6. 验证恢复
   ↓
7. 故障复盘
   ↓
8. 编写故障报告
```

### 故障发现渠道

- **监控系统告警**：Prometheus + Grafana
- **用户反馈**：客服工单、邮件、电话
- **日志异常**：ELK Stack 日志分析
- **巡检发现**：定期系统巡检
- **自动化检测**：健康检查脚本

---

## 故障分级标准

| 级别 | 定义 | 响应时间 | 解决时间 | 示例 |
|------|------|----------|----------|------|
| **P0** | 系统完全不可用 | 15 分钟 | 2 小时 | 服务崩溃、数据库损坏、网络中断 |
| **P1** | 核心功能不可用 | 30 分钟 | 4 小时 | API 无法访问、LNN 引擎故障、认证失败 |
| **P2** | 部分功能异常 | 2 小时 | 24 小时 | 某些 API 报错、性能下降、非核心功能异常 |
| **P3** | 轻微问题 | 24 小时 | 72 小时 | 日志警告、UI 显示问题、文档错误 |

---

## 常见故障处理

### 故障 1：服务无法启动

**症状**：
```
docker-compose up 失败
错误：Address already in use
错误：Cannot start service api
```

**诊断步骤**：
```bash
# 1. 检查端口占用
netstat -tlnp | grep 8765
# 或
lsof -i :8765

# 2. 检查进程
ps aux | grep uvicorn
ps aux | grep python

# 3. 查看 Docker 日志
docker-compose logs api

# 4. 检查配置文件
cat config/settings.yaml | grep port
```

**解决方案**：

**方案 A：停止占用端口的进程**
```bash
# 找到占用端口的进程 ID
PID=$(lsof -ti :8765)

# 停止进程
kill -15 $PID

# 如果进程无法停止
kill -9 $PID

# 重启服务
docker-compose restart
```

**方案 B：修改服务端口**
```bash
# 编辑配置文件
vim config/settings.yaml

# 修改端口
server:
  port: 8001  # 改为其他端口

# 重启服务
docker-compose restart
```

**验证**：
```bash
# 检查服务状态
docker-compose ps

# 健康检查
curl http://localhost:8765/api/health/ping
```

---

### 故障 2：数据库连接失败

**症状**：
```
Error: database is locked
Error: unable to open database file
sqlite3.OperationalError: database disk image is malformed
```

**诊断步骤**：
```bash
# 1. 检查数据库文件
ls -lh data/app.db
ls -lh data/app.db-wal
ls -lh data/app.db-shm

# 2. 检查文件权限
stat data/app.db

# 3. 检查磁盘空间
df -h

# 4. 检查数据库完整性
sqlite3 data/app.db "PRAGMA integrity_check;"

# 5. 查看数据库日志
tail -f logs/database.log
```

**解决方案**：

**方案 A：修复权限问题**
```bash
# 修复文件权限
chmod 664 data/app.db
chmod 664 data/app.db-wal
chmod 664 data/app.db-shm

# 修复目录权限
chmod 775 data/

# 重启服务
docker-compose restart
```

**方案 B：解锁数据库**
```bash
# 停止所有服务
docker-compose stop

# 删除锁文件（如果存在）
rm -f data/app.db.lock

# 检查并修复 WAL 文件
sqlite3 data/app.db "PRAGMA wal_checkpoint(TRUNCATE);"

# 启动服务
docker-compose start
```

**方案 C：恢复数据库**
```bash
# 1. 备份当前数据库
cp data/app.db data/app.db.corrupted

# 2. 从备份恢复
cp backup/latest.db data/app.db

# 3. 验证恢复
sqlite3 data/app.db "PRAGMA integrity_check;"

# 4. 重启服务
docker-compose restart
```

**方案 D：修复损坏的数据库**
```bash
# 导出数据库
sqlite3 data/app.db ".dump" > backup.sql

# 创建新数据库
rm data/app.db
sqlite3 data/app.db < backup.sql

# 验证
sqlite3 data/app.db "PRAGMA integrity_check;"
```

**验证**：
```bash
# 测试数据库连接
curl http://localhost:8765/api/health/ping

# 检查数据库状态
sqlite3 data/app.db "SELECT COUNT(*) FROM tasks;"
```

---

### 故障 3：LNN 引擎推理超时

**症状**：
```
Error: LNN inference timeout
响应时间 > 30 秒
Error: Model loading failed
```

**诊断步骤**：
```bash
# 1. 检查 LNN 引擎状态
curl http://localhost:8765/api/health/ping

# 2. 检查模型加载情况
curl http://localhost:8765/api/v1/lnn/models

# 3. 查看 LNN 日志
tail -f logs/lnn.log
tail -f logs/lnn_error.log

# 4. 检查系统资源
top
free -h
df -h

# 5. 检查模型文件
ls -lh models/
```

**解决方案**：

**方案 A：重启 LNN 引擎**
```bash
# 重启 LNN 服务
docker-compose restart lnn-engine

# 或
systemctl restart lingjing-lnn

# 等待引擎启动
sleep 10

# 验证
curl http://localhost:8765/api/health/ping
```

**方案 B：清理模型缓存**
```bash
# 清理缓存
curl -X POST http://localhost:8765/api/v1/lnn/cache/clear

# 重新加载模型
curl -X POST http://localhost:8765/api/v1/lnn/models/reload
```

**方案 C：调整超时配置**
```bash
# 编辑配置
vim config/lnn_workflow.yaml

# 增加超时时间
lnn:
  inference_timeout: 60  # 从 30 增加到 60
  model_load_timeout: 120

# 重启服务
docker-compose restart
```

**方案 D：优化模型**
```bash
# 检查模型大小
du -sh models/*

# 压缩模型（如需要）
python scripts/optimize_model.py --model tool_wear_v1

# 重新训练（如需要）
python scripts/train_lnn_model.py --config config/lnn_workflow.yaml
```

**验证**：
```bash
# 测试推理
curl -X POST http://localhost:8765/api/v1/lnn/predict \
  -H "Content-Type: application/json" \
  -d '{"features": [1.0, 2.0, 3.0]}'

# 检查响应时间
time curl http://localhost:8765/api/v1/lnn/models
```

---

### 故障 4：内存溢出（OOM）

**症状**：
```
Error: Out of memory
Killed
服务自动重启
系统响应缓慢
```

**诊断步骤**：
```bash
# 1. 检查内存使用
free -h
cat /proc/meminfo

# 2. 检查进程内存占用
ps aux --sort=-%mem | head -20

# 3. 检查系统日志
dmesg | grep -i "out of memory"
journalctl -k | grep -i oom

# 4. 检查应用日志
tail -f logs/app.log | grep -i memory
```

**解决方案**：

**方案 A：紧急释放内存**
```bash
# 清理系统缓存
sync
echo 3 > /proc/sys/vm/drop_caches

# 停止非关键服务
docker-compose stop worker
docker-compose stop scheduler

# 重启主服务
docker-compose restart api
```

**方案 B：调整应用配置**
```bash
# 减少 workers 数量
vim config/settings.yaml
server:
  workers: 2  # 从 4 减少到 2

# 减少模型缓存
vim config/lnn_workflow.yaml
lnn:
  model_cache_size: 50  # 从 100 减少到 50

# 减少批处理大小
lnn:
  batch_size: 16  # 从 32 减少到 16

# 重启服务
docker-compose restart
```

**方案 C：增加系统内存**
```bash
# 云环境：升级实例规格
# AWS
aws ec2 modify-instance-attribute --instance-id i-xxx --instance-type t3.large

# 阿里云
# 通过控制台升级实例规格

# 物理机：添加内存条
```

**方案 D：内存泄漏排查**
```bash
# 使用 memory_profiler
pip install memory-profiler

# 在代码中添加装饰器
from memory_profiler import profile

@profile
def process_data():
    # 可疑代码
    pass

# 运行并分析
python -m memory_profiler script.py
```

**验证**：
```bash
# 监控内存使用
watch -n 1 free -h

# 检查服务状态
docker-compose ps

# 压力测试
ab -n 100 -c 10 http://localhost:8765/api/health/ping
```

---

### 故障 5：API 响应缓慢

**症状**：
```
响应时间 > 5 秒
用户投诉系统卡顿
超时错误增加
```

**诊断步骤**：
```bash
# 1. 检查慢查询日志
tail -f logs/slow_queries.log

# 2. 检查数据库性能
sqlite3 data/app.db "EXPLAIN QUERY PLAN SELECT * FROM tasks;"

# 3. 检查系统负载
top
uptime

# 4. 检查网络延迟
ping localhost
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8765/api/health/ping

# 5. 检查 API 响应时间
curl -o /dev/null -s -w "Time: %{time_total}s\n" http://localhost:8765/api/v1/users
```

**解决方案**：

**方案 A：优化数据库查询**
```bash
# 分析慢查询
sqlite3 data/app.db <<EOF
.mode column
.headers on
EXPLAIN QUERY PLAN
SELECT * FROM tasks WHERE status='running' ORDER BY created_at DESC;
EOF

# 创建索引
sqlite3 data/app.db <<EOF
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
EOF

# 优化查询
# 避免 SELECT *
# 使用分页
# 避免在 WHERE 中使用函数
```

**方案 B：启用缓存**
```bash
# 编辑配置
vim config/settings.yaml

cache:
  enabled: true
  backend: redis
  ttl: 3600
  max_size: 1000

# 重启服务
docker-compose restart
```

**方案 C：清理历史数据**
```bash
# 清理旧日志
sqlite3 data/app.db <<EOF
DELETE FROM logs WHERE created_at < date('now', '-90 days');
VACUUM;
EOF

# 清理已完成任务
sqlite3 data/app.db <<EOF
DELETE FROM tasks 
WHERE status='completed' 
AND completed_at < date('now', '-30 days');
VACUUM;
EOF
```

**方案 D：增加资源**
```bash
# 增加 workers
vim config/settings.yaml
server:
  workers: 8  # 增加 worker 数量

# 使用负载均衡
# 部署多个实例
docker-compose up -d --scale api=3
```

**验证**：
```bash
# 测试响应时间
for i in {1..10}; do
  curl -o /dev/null -s -w "%{time_total}\n" http://localhost:8765/api/health/ping
done

# 压力测试
ab -n 1000 -c 50 http://localhost:8765/api/health/ping
```

---

### 故障 6：认证失败

**症状**：
```
Error: Invalid token
Error: Authentication failed
401 Unauthorized
用户无法登录
```

**诊断步骤**：
```bash
# 1. 检查认证日志
tail -f logs/auth.log

# 2. 检查 JWT 配置
cat config/settings.yaml | grep jwt

# 3. 检查用户数据库
sqlite3 data/app.db "SELECT * FROM users WHERE username='testuser';"

# 4. 检查 Redis（如果使用）
redis-cli ping
redis-cli keys "*token*"
```

**解决方案**：

**方案 A：重置用户密码**
```bash
cd python
python scripts/reset_admin_password.py

# 或手动重置
sqlite3 data/app.db <<EOF
UPDATE users 
SET password_hash = '$2b$12$...' 
WHERE username = 'admin';
EOF
```

**方案 B：刷新 JWT 密钥**
```bash
# 生成新密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 更新配置
vim .env
JWT_SECRET=new-secret-key

# 重启服务（会使所有 token 失效）
docker-compose restart
```

**方案 C：清理过期 token**
```bash
# 如果使用 Redis
redis-cli keys "*token*" | xargs redis-cli del

# 清理数据库中的过期 token
sqlite3 data/app.db <<EOF
DELETE FROM tokens WHERE expires_at < datetime('now');
EOF
```

**验证**：
```bash
# 测试登录
curl -X POST http://localhost:8765/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password"}'

# 使用 token 访问受保护资源
TOKEN="your-token-here"
curl http://localhost:8765/api/v1/users/me \
  -H "Authorization: Bearer $TOKEN"
```

---

### 故障 7：磁盘空间不足

**症状**：
```
No space left on device
Error: unable to create file
数据库写入失败
日志写入失败
```

**诊断步骤**：
```bash
# 1. 检查磁盘使用
df -h

# 2. 查找大文件
du -sh /* | sort -rh | head -20
du -sh /var/log/* | sort -rh | head -10

# 3. 检查 Docker 使用
docker system df

# 4. 检查数据库大小
ls -lh data/
du -sh data/
```

**解决方案**：

**方案 A：清理日志文件**
```bash
# 压缩旧日志
find logs/ -name "*.log" -mtime +7 -exec gzip {} \;

# 删除旧日志
find logs/ -name "*.log.gz" -mtime +30 -delete

# 清空当前日志（谨慎使用）
> logs/app.log
> logs/error.log
```

**方案 B：清理 Docker**
```bash
# 清理未使用的资源
docker system prune -a

# 清理旧镜像
docker image prune -a

# 清理未使用的卷
docker volume prune
```

**方案 C：清理历史数据**
```bash
# 清理旧备份
find backup/ -name "*.db.gz" -mtime +30 -delete

# 清理临时文件
rm -rf /tmp/lingjing-*

# 清理数据库历史数据
sqlite3 data/app.db <<EOF
DELETE FROM logs WHERE created_at < date('now', '-90 days');
VACUUM;
EOF
```

**方案 D：扩展磁盘**
```bash
# 云环境：扩展磁盘
# AWS
aws ec2 modify-volume --volume-id vol-xxx --size 200

# 扩展文件系统
sudo resize2fs /dev/xvda1

# 物理机：添加硬盘
```

**验证**：
```bash
# 检查磁盘空间
df -h

# 测试写入
touch test_file && rm test_file

# 检查服务状态
docker-compose ps
```

---

## 故障诊断工具

### 系统诊断

```bash
# 系统资源监控
top                    # CPU 和内存使用
htop                   # 交互式进程查看器
vmstat 1               # 虚拟内存统计
iostat 1               # IO 统计

# 网络诊断
netstat -tlnp          # 端口占用
ss -tlnp               # 端口占用（推荐）
tcpdump -i eth0        # 网络抓包
ping localhost         # 网络连通性

# 磁盘诊断
df -h                  # 磁盘使用
du -sh *               # 目录大小
iostat -x 1            # 磁盘 IO
```

### 应用诊断

```bash
# API 测试
curl -v http://localhost:8765/api/health/ping
ab -n 100 -c 10 http://localhost:8765/api/health/ping

# 数据库诊断
sqlite3 data/app.db "PRAGMA integrity_check;"
sqlite3 data/app.db ".tables"
sqlite3 data/app.db ".schema"

# 日志分析
tail -f logs/app.log
grep "ERROR" logs/app.log | tail -20
grep "Exception" logs/app.log | tail -20
```

### 性能分析

```bash
# Python 性能分析
python -m cProfile -s time script.py
python -m memory_profiler script.py

# 数据库性能分析
sqlite3 data/app.db <<EOF
.timer on
SELECT * FROM tasks WHERE status='running';
EOF
```

---

## 故障升级机制

### 升级条件

| 条件 | 升级动作 |
|------|----------|
| P0 故障 15 分钟未解决 | 升级到技术总监 |
| P1 故障 30 分钟未解决 | 升级到运维经理 |
| P2 故障 2 小时未解决 | 升级到运维团队 |
| 故障影响范围扩大 | 立即升级 |
| 需要跨团队协作 | 升级到协调人 |

### 升级联系人

| 角色 | 姓名 | 电话 | 邮箱 |
|------|------|------|------|
| 运维负责人 | 张三 | 138-xxxx-xxxx | zhangsan@company.com |
| 技术总监 | 李四 | 139-xxxx-xxxx | lisi@company.com |
| 开发负责人 | 王五 | 137-xxxx-xxxx | wangwu@company.com |

### 升级流程

```
1. 判断是否需要升级
   ↓
2. 通知上级联系人
   ↓
3. 提供故障信息
   - 故障现象
   - 已采取措施
   - 需要的支持
   ↓
4. 协同处理
   ↓
5. 记录升级过程
```

---

## 故障报告模板

```markdown
# 故障报告

**故障编号**: INC-YYYY-NNN
**故障时间**: YYYY-MM-DD HH:MM - HH:MM
**故障级别**: P0/P1/P2/P3
**影响范围**: 描述影响的用户/功能范围
**故障时长**: X 小时 X 分钟

## 故障描述

{详细描述故障现象，包括：
- 用户反馈
- 监控告警
- 错误信息}

## 时间线

| 时间 | 事件 | 负责人 |
|------|------|--------|
| HH:MM | 发现故障 | 张三 |
| HH:MM | 初步诊断 | 张三 |
| HH:MM | 开始处理 | 李四 |
| HH:MM | 故障恢复 | 李四 |
| HH:MM | 验证完成 | 王五 |

## 根本原因

{详细分析故障的根本原因，包括：
- 直接原因
- 间接原因
- 深层原因}

## 处理过程

{详细描述处理步骤，包括：
- 采取的措施
- 使用的工具
- 遇到的困难}

## 影响评估

- **影响用户数**: X 人
- **影响功能**: 列出受影响的功能
- **数据损失**: 描述是否有数据损失
- **业务损失**: 估算业务损失

## 改进措施

### 短期措施（1 周内）
1. {措施 1} - 负责人：张三 - 截止日期：YYYY-MM-DD
2. {措施 2} - 负责人：李四 - 截止日期：YYYY-MM-DD

### 长期措施（1 月内）
1. {措施 1} - 负责人：王五 - 截止日期：YYYY-MM-DD
2. {措施 2} - 负责人：赵六 - 截止日期：YYYY-MM-DD

## 经验教训

{总结经验教训，包括：
- 做得好的方面
- 需要改进的方面
- 可复用的经验}

## 附件

- 相关日志文件
- 监控截图
- 其他证据

---

**报告人**: 张三
**报告日期**: YYYY-MM-DD
**审核人**: 李四
**审核日期**: YYYY-MM-DD
```

---

## 附录

### 常用命令速查

```bash
# 服务管理
docker-compose ps              # 查看服务状态
docker-compose logs -f         # 查看实时日志
docker-compose restart         # 重启服务
docker-compose down            # 停止服务
docker-compose up -d           # 启动服务

# 数据库操作
sqlite3 data/app.db       # 进入数据库
.tables                        # 查看所有表
.schema                        # 查看表结构
PRAGMA integrity_check;        # 检查完整性
.backup backup.db              # 备份数据库

# 系统诊断
top                            # CPU 和内存
df -h                          # 磁盘空间
free -h                        # 内存使用
netstat -tlnp                  # 端口占用
```

### 相关文档

- [运维手册 README](./README.md)
- [备份恢复指南](./backup-recovery.md)

---

**最后更新**: 2026-07-10  
**维护者**: 运维团队
