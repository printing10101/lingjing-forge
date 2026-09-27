#!/bin/bash
# PostgreSQL 自动备份脚本
# 用于 docker-compose 环境中的定期备份

# P2-5 修复：
#   1. 添加 pipefail，确保管道中 pg_dump 失败不会被 gzip 成功掩盖
#   2. 校验 POSTGRES_PASSWORD 必须存在，空密码直接失败而非产生无效备份
#   3. 备份失败时通过 trap 清理 0 字节的占位文件，避免误以为有备份
set -o pipefail

# 配置
BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.sql.gz"
RETENTION_DAYS=7

# P2-5 修复：失败时清理占位文件（trap 在 EXIT 时触发，根据退出码决定是否清理）
cleanup() {
    local rc=$?
    if [ $rc -ne 0 ] && [ -f "${BACKUP_FILE}" ]; then
        # 备份失败时清理 0 字节或残缺的占位文件
        local size=$(stat -c%s "${BACKUP_FILE}" 2>/dev/null || echo 0)
        if [ "${size}" -lt 100 ]; then
            rm -f "${BACKUP_FILE}"
            echo "[$(date)] 已清理残缺的备份文件: ${BACKUP_FILE}" >&2
        fi
    fi
    exit $rc
}
trap cleanup EXIT

# 确保备份目录存在
mkdir -p ${BACKUP_DIR}

# 从环境变量读取数据库配置
DB_HOST=${POSTGRES_HOST:-postgres}
DB_PORT=${POSTGRES_PORT:-5432}
DB_NAME=${POSTGRES_DB:-lingjing}
DB_USER=${POSTGRES_USER:-postgres}

# P2-5 修复：校验密码必须存在，避免空密码产生无效备份
if [ -z "${POSTGRES_PASSWORD}" ]; then
    echo "[$(date)] 备份失败: POSTGRES_PASSWORD 环境变量未设置!" >&2
    exit 1
fi

echo "[$(date)] 开始备份数据库 ${DB_NAME}..."

# 执行备份并压缩
# P2-5 修复：pipefail 确保 pg_dump 失败时整个管道返回非零
PGPASSWORD=${POSTGRES_PASSWORD} pg_dump \
    -h ${DB_HOST} \
    -p ${DB_PORT} \
    -U ${DB_USER} \
    -d ${DB_NAME} \
    --format=custom \
    --blobs \
    --verbose \
    | gzip > ${BACKUP_FILE}

if [ $? -eq 0 ]; then
    echo "[$(date)] 备份成功: ${BACKUP_FILE}"
    echo "[$(date)] 备份大小: $(du -h ${BACKUP_FILE} | cut -f1)"
else
    echo "[$(date)] 备份失败!" >&2
    exit 1
fi

# 清理旧备份
echo "[$(date)] 清理 ${RETENTION_DAYS} 天前的备份文件..."
find ${BACKUP_DIR} -name "backup_*.sql.gz" -type f -mtime +${RETENTION_DAYS} -delete

# 列出当前所有备份
echo "[$(date)] 当前备份列表:"
ls -lh ${BACKUP_DIR}/backup_*.sql.gz 2>/dev/null || echo "无备份文件"

echo "[$(date)] 备份任务完成"
