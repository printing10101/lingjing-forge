#!/bin/sh
# ==============================================================================
# P0-15 修复：开发环境自签名 TLS 证书生成脚本
# ==============================================================================
# 用途：在 deploy/nginx/certs/ 目录下生成自签名证书，避免 nginx 因证书缺失启动失败。
#
# 使用场景：
#   - 本地开发/测试环境（浏览器会显示安全警告，属正常现象）
#   - docker compose up 前需要先有证书才能让 nginx 启动
#
# 不适用场景：
#   - 生产环境必须使用 Let's Encrypt / 阿里云 SSL / 企业 CA 签发的真实证书
#   - 详见 deploy/nginx/README_TLS.md
#
# 使用方法：
#   cd deploy/nginx
#   sh generate_dev_cert.sh
#   或：
#   sh deploy/nginx/generate_dev_cert.sh
# ==============================================================================

set -e

# 脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/certs"

mkdir -p "${CERTS_DIR}"

FULLCHAIN="${CERTS_DIR}/fullchain.pem"
PRIVKEY="${CERTS_DIR}/privkey.pem"

# 若证书已存在，不覆盖
if [ -f "${FULLCHAIN}" ] && [ -f "${PRIVKEY}" ]; then
    echo "[INFO] 证书已存在，跳过生成："
    echo "  - ${FULLCHAIN}"
    echo "  - ${PRIVKEY}"
    echo "[INFO] 如需重新生成，请先删除上述文件。"
    exit 0
fi

# 检查 openssl 是否可用
if ! command -v openssl >/dev/null 2>&1; then
    echo "[ERROR] 未找到 openssl 命令，请先安装 openssl：" 1>&2
    echo "  Ubuntu/Debian: sudo apt-get install -y openssl" 1>&2
    echo "  CentOS/RHEL:   sudo yum install -y openssl" 1>&2
    exit 1
fi

echo "[INFO] 正在生成自签名证书（有效期 365 天）..."

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "${PRIVKEY}" \
    -out "${FULLCHAIN}" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=Lingjing-Dev/CN=localhost" \
    >/dev/null 2>&1

chmod 644 "${FULLCHAIN}"
chmod 600 "${PRIVKEY}"

echo "[INFO] 自签名证书已生成："
echo "  - ${FULLCHAIN}"
echo "  - ${PRIVKEY}"
echo ""
echo "[WARNING] 自签名证书仅用于开发/测试环境，浏览器会显示安全警告。" 1>&2
echo "[WARNING] 生产环境请使用真实证书，详见 README_TLS.md。" 1>&2
