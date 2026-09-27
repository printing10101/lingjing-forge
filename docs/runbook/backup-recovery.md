# 备份与恢复指南

**版本**: 1.0.0  
**最后更新**: 2026-07-10  
**适用范围**: 灵境制造系统所有运维人员

---

## 目录

1. [备份策略](#备份策略)
2. [自动备份配置](#自动备份配置)
3. [手动备份操作](#手动备份操作)
4. [恢复流程](#恢复流程)
5. [备份验证](#备份验证)
6. [备份存储](#备份存储)
7. [灾难恢复](#灾难恢复)

---

## 备份策略

### 备份类型

| 类型 | 频率 | 保留时间 | 存储位置 | 说明 |
|------|------|----------|----------|------|
| **完整备份** | 每天 02:00 | 30 天 | 本地 + S3 | 全量数据库备份 |
| **增量备份** | 每小时 | 7 天 | 本地 | WAL 日志备份 |
| **配置备份** | 每天 03:00 | 90 天 | 本地 + S3 | 配置文件备份 |
| **日志备份** | 每天 04:00 | 30 天 | 本地 | 日志文件压缩备份 |

### RPO 和 RTO

- **RPO（恢复点目标）**：1 小时
  - 最多丢失 1 小时的数据
- **RTO（恢复时间目标）**：30 分钟
  - 从故障到恢复的时间不超过 30 分钟

### 备份范围

**必须备份**：
- SQLite 数据库文件（`data/app.db`）
- WAL 日志文件（`data/app.db-wal`）
- 配置文件（`config/`）
- 环境变量（`.env`）
- AI 模型文件（`models/`）
- 用户上传文件（`uploads/`）

**可选备份**：
- 应用日志（`logs/`）
- 临时文件（`tmp/`）
- 缓存文件（`cache/`）

**不需要备份**：
- `node_modules/`
- `__pycache__/`
- `.venv/`
- 构建产物（`dist/`、`build/`）

---

## 自动备份配置

### 备份脚本

创建备份脚本 `scripts/backup.sh`：

```bash
#!/bin/bash
# 灵境制造系统自动备份脚本

set -e  # 遇到错误立即退出

# 配置
BACKUP_DIR="/backup/lingjing"
DATE=$(date +%Y%m%d_%H%M%S)
DB_PATH="./data/app.db"
CONFIG_DIR="./config"
MODEL_DIR="./models"
LOG_DIR="./logs"

# 创建备份目录
mkdir -p $BACKUP_DIR/database
mkdir -p $BACKUP_DIR/config
mkdir -p $BACKUP_DIR/models
mkdir -p $BACKUP_DIR/logs

echo "[$(date)] 开始备份..."

# 1. 备份数据库
echo "[$(date)] 备份数据库..."
sqlite3 $DB_PATH ".backup '$BACKUP_DIR/database/lingjing_$DATE.db'"
gzip $BACKUP_DIR/database/lingjing_$DATE.db
echo "[$(date)] 数据库备份完成"

# 2. 备份配置文件
echo "[$(date)] 备份配置文件..."
tar -czf $BACKUP_DIR/config/config_$DATE.tar.gz \
  -C $(dirname $CONFIG_DIR) \
  $(basename $CONFIG_DIR)
echo "[$(date)] 配置文件备份完成"

# 3. 备份模型文件
echo "[$(date)] 备份模型文件..."
if [ -d "$MODEL_DIR" ]; then
  tar -czf $BACKUP_DIR/models/models_$DATE.tar.gz \
    -C $(dirname $MODEL_DIR) \
    $(basename $MODEL_DIR)
  echo "[$(date)] 模型文件备份完成"
fi

# 4. 备份日志文件
echo "[$(date)] 备份日志文件..."
if [ -d "$LOG_DIR" ]; then
  tar -czf $BACKUP_DIR/logs/logs_$DATE.tar.gz \
    -C $(dirname $LOG_DIR) \
    $(basename $LOG_DIR)
  echo "[$(date)] 日志文件备份完成"
fi

# 5. 创建最新备份符号链接
ln -sf $BACKUP_DIR/database/lingjing_$DATE.db.gz $BACKUP_DIR/database/latest.db.gz
ln -sf $BACKUP_DIR/config/config_$DATE.tar.gz $BACKUP_DIR/config/latest.tar.gz

# 6. 清理旧备份
echo "[$(date)] 清理旧备份..."
find $BACKUP_DIR/database -name "lingjing_*.db.gz" -mtime +30 -delete
find $BACKUP_DIR/config -name "config_*.tar.gz" -mtime +90 -delete
find $BACKUP_DIR/models -name "models_*.tar.gz" -mtime +90 -delete
find $BACKUP_DIR/logs -name "logs_*.tar.gz" -mtime +30 -delete
echo "[$(date)] 旧备份清理完成"

# 7. 上传到 S3（可选）
if [ -n "$AWS_S3_BUCKET" ]; then
  echo "[$(date)] 上传到 S3..."
  aws s3 cp $BACKUP_DIR/database/lingjing_$DATE.db.gz \
    s3://$AWS_S3_BUCKET/backups/database/
  aws s3 cp $BACKUP_DIR/config/config_$DATE.tar.gz \
    s3://$AWS_S3_BUCKET/backups/config/
  echo "[$(date)] S3 上传完成"
fi

echo "[$(date)] 备份完成！"
echo "备份文件位置："
echo "  数据库: $BACKUP_DIR/database/lingjing_$DATE.db.gz"
echo "  配置: $BACKUP_DIR/config/config_$DATE.tar.gz"
echo "  模型: $BACKUP_DIR/models/models_$DATE.tar.gz"
echo "  日志: $BACKUP_DIR/logs/logs_$DATE.tar.gz"
```

### 配置定时任务

```bash
# 编辑 crontab
crontab -e

# 添加以下任务
# 每天凌晨 2 点执行完整备份
0 2 * * * /path/to/scripts/backup.sh >> /var/log/lingjing-backup.log 2>&1

# 每小时执行增量备份（WAL 日志）
0 * * * * /path/to/scripts/backup_wal.sh >> /var/log/lingjing-backup.log 2>&1

# 每天凌晨 3 点备份配置文件
0 3 * * * /path/to/scripts/backup_config.sh >> /var/log/lingjing-backup.log 2>&1
```

### WAL 增量备份脚本

创建 `scripts/backup_wal.sh`：

```bash
#!/bin/bash
# WAL 日志增量备份

set -e

BACKUP_DIR="/backup/lingjing/wal"
DATE=$(date +%Y%m%d_%H%M)
DB_PATH="./data/app.db"

mkdir -p $BACKUP_DIR

# 检查 WAL 文件是否存在
if [ -f "$DB_PATH-wal" ]; then
  # 复制 WAL 文件
  cp $DB_PATH-wal $BACKUP_DIR/lingjing_$DATE.wal
  
  # 压缩
  gzip $BACKUP_DIR/lingjing_$DATE.wal
  
  # 清理 7 天前的 WAL 备份
  find $BACKUP_DIR -name "lingjing_*.wal.gz" -mtime +7 -delete
  
  echo "[$(date)] WAL 备份完成: lingjing_$DATE.wal.gz"
fi
```

---

## 手动备份操作

### 完整备份

```bash
# 1. 停止服务（可选，确保数据一致性）
docker-compose stop

# 2. 备份数据库
sqlite3 data/app.db ".backup 'backup/lingjing_$(date +%Y%m%d_%H%M).db'"
gzip backup/lingjing_$(date +%Y%m%d_%H%M).db

# 3. 备份配置文件
tar -czf backup/config_$(date +%Y%m%d_%H%M).tar.gz config/

# 4. 备份模型文件
tar -czf backup/models_$(date +%Y%m%d_%H%M).tar.gz models/

# 5. 启动服务
docker-compose start

# 6. 验证备份
sqlite3 backup/lingjing_$(date +%Y%m%d_%H%M).db "PRAGMA integrity_check;"
```

### 单表备份

```bash
# 备份单个表
sqlite3 data/app.db <<EOF
.mode csv
.output backup/users_$(date +%Y%m%d).csv
SELECT * FROM users;
.output stdout
EOF

# 备份表结构
sqlite3 data/app.db ".schema users" > backup/users_schema.sql
```

### 导出为 SQL

```bash
# 导出整个数据库为 SQL
sqlite3 data/app.db ".dump" > backup/lingjing_$(date +%Y%m%d).sql

# 导出特定表
sqlite3 data/app.db ".dump users" > backup/users_$(date +%Y%m%d).sql
```

---

## 恢复流程

### 完整恢复

**场景**：系统完全崩溃，需要从备份恢复

```bash
# 1. 停止所有服务
docker-compose stop

# 2. 备份当前数据（以防万一）
mv data/app.db data/app.db.backup.$(date +%Y%m%d_%H%M)

# 3. 恢复数据库
gunzip backup/lingjing_20240120_020000.db.gz
cp backup/lingjing_20240120_020000.db data/app.db

# 4. 验证数据库完整性
sqlite3 data/app.db "PRAGMA integrity_check;"
# 预期输出：ok

# 5. 恢复配置文件
tar -xzf backup/config_20240120_030000.tar.gz -C ./

# 6. 恢复模型文件
tar -xzf backup/models_20240120_020000.tar.gz -C ./

# 7. 启动服务
docker-compose start

# 8. 验证恢复
curl http://localhost:8765/api/health/ping
sqlite3 data/app.db "SELECT COUNT(*) FROM users;"
```

### 时间点恢复

**场景**：需要恢复到特定时间点的数据

```bash
# 1. 找到目标时间点的备份
ls -lh backup/database/
# 选择最接近目标时间的备份

# 2. 恢复数据库
gunzip backup/database/lingjing_20240120_020000.db.gz
cp backup/database/lingjing_20240120_020000.db data/app.db

# 3. 应用 WAL 日志（如果需要更精确的时间点）
# 找到目标时间点的 WAL 备份
ls -lh backup/wal/
# 假设需要应用到 10:30 的 WAL
gunzip backup/wal/lingjing_20240120_1030.wal.gz
cp backup/wal/lingjing_20240120_1030.wal data/app.db-wal

# 4. 启动服务（会自动应用 WAL）
docker-compose start

# 5. 验证数据
sqlite3 data/app.db "SELECT MAX(created_at) FROM tasks;"
```

### 单表恢复

**场景**：只需要恢复特定表的数据

```bash
# 1. 从备份中提取单表
sqlite3 backup/lingjing_20240120.db <<EOF
.mode csv
.output /tmp/users.csv
SELECT * FROM users;
.output stdout
EOF

# 2. 导入到当前数据库
sqlite3 data/app.db <<EOF
.mode csv
.import /tmp/users.csv users
EOF

# 或使用 SQL 恢复
sqlite3 data/app.db <<EOF
ATTACH DATABASE 'backup/lingjing_20240120.db' AS backup_db;
INSERT OR REPLACE INTO users SELECT * FROM backup_db.users;
DETACH DATABASE backup_db;
EOF
```

### 从 S3 恢复

```bash
# 1. 从 S3 下载备份
aws s3 cp s3://your-bucket/backups/database/lingjing_20240120_020000.db.gz \
  backup/

# 2. 解压
gunzip backup/lingjing_20240120_020000.db.gz

# 3. 恢复
cp backup/lingjing_20240120_020000.db data/app.db

# 4. 验证
sqlite3 data/app.db "PRAGMA integrity_check;"
```

---

## 备份验证

### 完整性检查

```bash
# 检查数据库完整性
sqlite3 backup/lingjing_20240120.db "PRAGMA integrity_check;"
# 预期输出：ok

# 检查表结构
sqlite3 backup/lingjing_20240120.db ".schema"

# 检查数据量
sqlite3 backup/lingjing_20240120.db <<EOF
SELECT 'users' as table_name, COUNT(*) as row_count FROM users
UNION ALL
SELECT 'tasks', COUNT(*) FROM tasks
UNION ALL
SELECT 'logs', COUNT(*) FROM logs;
EOF
```

### 恢复测试

**定期测试恢复流程**（建议每月一次）：

```bash
# 1. 创建测试环境
mkdir -p /tmp/restore-test
cd /tmp/restore-test

# 2. 恢复备份
cp /backup/lingjing_20240120.db ./test.db

# 3. 验证数据
sqlite3 test.db <<EOF
PRAGMA integrity_check;
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM tasks;
EOF

# 4. 测试关键功能
# 启动测试服务，验证核心功能是否正常

# 5. 清理测试环境
cd -
rm -rf /tmp/restore-test
```

### 自动化验证脚本

创建 `scripts/verify_backup.sh`：

```bash
#!/bin/bash
# 备份验证脚本

set -e

BACKUP_DIR="/backup/lingjing/database"
LATEST_BACKUP=$(ls -t $BACKUP_DIR/lingjing_*.db.gz | head -1)

echo "验证最新备份: $LATEST_BACKUP"

# 解压
gunzip -c $LATEST_BACKUP > /tmp/verify.db

# 完整性检查
RESULT=$(sqlite3 /tmp/verify.db "PRAGMA integrity_check;")
if [ "$RESULT" != "ok" ]; then
  echo "备份完整性检查失败！"
  exit 1
fi

# 检查关键表
TABLES=$(sqlite3 /tmp/verify.db ".tables")
REQUIRED_TABLES="users tasks logs"

for table in $REQUIRED_TABLES; do
  if ! echo "$TABLES" | grep -q "$table"; then
    echo "缺少关键表: $table"
    exit 1
  fi
done

# 检查数据量
USER_COUNT=$(sqlite3 /tmp/verify.db "SELECT COUNT(*) FROM users;")
if [ "$USER_COUNT" -eq 0 ]; then
  echo "警告: users 表为空"
fi

echo "备份验证通过"
echo "  用户数: $USER_COUNT"
echo "  备份大小: $(du -h $LATEST_BACKUP | cut -f1)"

# 清理
rm -f /tmp/verify.db
```

---

## 备份存储

### 本地存储

```bash
# 备份目录结构
/backup/lingjing/
├── database/           # 数据库备份
│   ├── lingjing_20240120_020000.db.gz
│   ├── lingjing_20240121_020000.db.gz
│   └── latest.db.gz -> lingjing_20240121_020000.db.gz
├── config/            # 配置备份
│   ├── config_20240120_030000.tar.gz
│   └── latest.tar.gz -> config_20240121_030000.tar.gz
├── models/            # 模型备份
│   └── models_20240120_020000.tar.gz
├── logs/              # 日志备份
│   └── logs_20240120_040000.tar.gz
└── wal/               # WAL 增量备份
    ├── lingjing_20240120_1000.wal.gz
    └── lingjing_20240120_1100.wal.gz
```

### 云存储（S3）

```bash
# 配置 AWS CLI
aws configure

# 上传备份
aws s3 cp backup/lingjing_20240120.db.gz \
  s3://your-bucket/backups/database/

# 设置生命周期策略
aws s3api put-bucket-lifecycle-configuration \
  --bucket your-bucket \
  --lifecycle-configuration '{
    "Rules": [
      {
        "ID": "BackupRetention",
        "Prefix": "backups/",
        "Status": "Enabled",
        "Expiration": {
          "Days": 90
        }
      }
    ]
  }'
```

### 备份加密

```bash
# 使用 GPG 加密备份
gpg -c backup/lingjing_20240120.db

# 输入密码
# 生成 lingjing_20240120.db.gpg

# 解密
gpg backup/lingjing_20240120.db.gpg

# 使用 OpenSSL 加密
openssl enc -aes-256-cbc -salt \
  -in backup/lingjing_20240120.db \
  -out backup/lingjing_20240120.db.enc \
  -pass pass:your-password

# 解密
openssl enc -aes-256-cbc -d \
  -in backup/lingjing_20240120.db.enc \
  -out backup/lingjing_20240120.db \
  -pass pass:your-password
```

---

## 灾难恢复

### 灾难场景

| 场景 | 影响 | 恢复策略 |
|------|------|----------|
| **服务器故障** | 服务不可用 | 从备份恢复到新服务器 |
| **数据损坏** | 数据丢失 | 从最近备份恢复 |
| **勒索软件** | 数据加密 | 从离线备份恢复 |
| **自然灾害** | 全部丢失 | 从异地备份恢复 |
| **人为误操作** | 数据删除 | 从备份恢复特定数据 |

### 灾难恢复计划

#### 阶段 1：评估（0-30 分钟）

1. **评估损害**
   - 确定受影响系统
   - 评估数据丢失范围
   - 确定恢复优先级

2. **通知相关人员**
   - 通知运维团队
   - 通知管理层
   - 通知受影响用户

#### 阶段 2：恢复（30 分钟 - 4 小时）

1. **准备恢复环境**
   ```bash
   # 准备新服务器（如需要）
   # 安装操作系统
   # 安装依赖软件
   ```

2. **恢复数据**
   ```bash
   # 从备份恢复数据库
   # 从备份恢复配置
   # 从备份恢复模型
   ```

3. **验证恢复**
   ```bash
   # 检查数据完整性
   # 测试核心功能
   # 验证性能指标
   ```

#### 阶段 3：验证（4-6 小时）

1. **功能测试**
   - 测试所有核心功能
   - 测试 API 接口
   - 测试用户访问

2. **性能测试**
   - 压力测试
   - 负载测试
   - 响应时间测试

#### 阶段 4：恢复服务（6-8 小时）

1. **切换流量**
   ```bash
   # 更新 DNS
   # 更新负载均衡
   # 逐步恢复流量
   ```

2. **监控观察**
   - 密切监控系统
   - 观察错误日志
   - 收集用户反馈

### 灾难恢复演练

**建议每季度进行一次灾难恢复演练**

演练内容：
1. 模拟服务器故障
2. 从备份恢复系统
3. 验证恢复结果
4. 记录演练时间
5. 总结改进点

演练报告模板：
```markdown
# 灾难恢复演练报告

**演练日期**: YYYY-MM-DD
**演练场景**: 服务器完全故障
**参与人员**: 张三、李四、王五

## 演练时间线

| 时间 | 事件 | 耗时 |
|------|------|------|
| 09:00 | 开始演练 | - |
| 09:15 | 评估完成 | 15 分钟 |
| 09:45 | 环境准备完成 | 30 分钟 |
| 10:30 | 数据恢复完成 | 45 分钟 |
| 11:00 | 验证完成 | 30 分钟 |
| 11:30 | 服务恢复 | 30 分钟 |
| 12:00 | 演练结束 | 30 分钟 |

**总恢复时间**: 3 小时

## 演练结果

- 备份完整性验证通过
- 数据恢复成功
- 核心功能正常
- 性能略有下降

## 改进措施

1. 优化恢复脚本
2. 增加自动化验证
3. 完善文档

## 下次演练计划

**计划日期**: YYYY-MM-DD
**演练场景**: 数据损坏恢复
```

---

## 附录

### 备份命令速查

```bash
# 数据库备份
sqlite3 data/app.db ".backup backup.db"
sqlite3 data/app.db ".dump" > backup.sql

# 数据库恢复
sqlite3 data/app.db ".restore backup.db"
sqlite3 data/app.db < backup.sql

# 压缩备份
gzip backup.db
tar -czf backup.tar.gz data/

# 解压恢复
gunzip backup.db.gz
tar -xzf backup.tar.gz

# 加密备份
gpg -c backup.db
openssl enc -aes-256-cbc -salt -in backup.db -out backup.db.enc

# 验证备份
sqlite3 backup.db "PRAGMA integrity_check;"
```

### 相关文档

- [运维手册 README](./README.md)
- [故障处理手册](./troubleshooting.md)

---

**最后更新**: 2026-07-10  
**维护者**: 运维团队
