#!/usr/bin/env bash
# =============================================================================
# 灵境制造 - Linux 离线安装脚本
# =============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[√]${NC} $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; exit 1; }

echo "========================================"
echo " 灵境制造 - Linux 离线安装脚本"
echo "========================================"
echo

# 获取脚本所在目录（离线包根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------
# [1/5] 检查 Python
# ------------------------------------------------------------------
echo "[1/5] 检查 Python 环境..."
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    error "未检测到 Python，请先安装 Python 3.10+"
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
info "Python 版本: $PYTHON_VERSION"

MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
    error "Python 版本过低 ($PYTHON_VERSION)，需要 3.10+"
fi

# ------------------------------------------------------------------
# [2/5] 创建虚拟环境
# ------------------------------------------------------------------
echo
echo "[2/5] 创建 Python 虚拟环境..."
if [ ! -d "venv" ]; then
    $PYTHON_CMD -m venv venv
    info "虚拟环境创建成功"
else
    info "虚拟环境已存在"
fi

source venv/bin/activate

# ------------------------------------------------------------------
# [3/5] 从 wheels/ 离线安装依赖
# ------------------------------------------------------------------
echo
echo "[3/5] 离线安装 Python 依赖..."

if [ ! -d "wheels" ]; then
    error "未找到 wheels 目录，请确认离线包完整性"
fi

pip install --upgrade pip --no-index --find-links=wheels -q

pip install --no-index --find-links=wheels -r requirements.txt
if [ $? -ne 0 ]; then
    error "离线依赖安装失败，请确认 wheels 目录完整"
fi
info "依赖安装完成"

# ------------------------------------------------------------------
# [4/5] 加载 Docker 镜像
# ------------------------------------------------------------------
echo
echo "[4/5] 加载 Docker 镜像..."
if [ -d "docker_images" ] && [ "$(ls -A docker_images/*.tar 2>/dev/null)" ]; then
    if command -v docker &>/dev/null; then
        for img in docker_images/*.tar; do
            echo "  正在加载: $(basename "$img")"
            docker load -i "$img" 2>/dev/null && \
                info "镜像 $(basename "$img") 加载成功" || \
                warn "镜像 $(basename "$img") 加载失败"
        done
        info "Docker 镜像加载完成"
    else
        warn "未检测到 Docker，跳过镜像加载"
    fi
else
    info "未包含 Docker 镜像，跳过"
fi

# ------------------------------------------------------------------
# [5/5] 初始化并启动服务
# ------------------------------------------------------------------
echo
echo "[5/5] 初始化数据库..."

# 复制环境配置文件
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    info "已从 .env.example 创建 .env 配置文件"
    warn "请根据实际情况修改 .env 中的配置项"
fi

cd python
# P0-7 修复：engine 与 init_db 定义在 app.database.models 模块中，
# 非 app.database；且 init_db 为 async 函数，需用 asyncio.run 驱动。
$PYTHON_CMD -c "
import asyncio
from app.database.models import init_db
asyncio.run(init_db())
print('数据库初始化完成')
" 2>/dev/null && info "数据库初始化完成" || warn "数据库初始化失败，请检查 .env 配置"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------
# 检测 systemd 并创建服务
# ------------------------------------------------------------------
echo
echo "========================================"
echo " 离线安装完成！"
echo "========================================"
echo
echo " 启动方式："
echo "   手动启动:  cd $SCRIPT_DIR/python && ../venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765"
echo "   服务地址:  http://localhost:8765"
echo "   API 文档:  http://localhost:8765/docs"
echo
if command -v docker &>/dev/null && [ -d "docker_images" ]; then
    echo " Docker 启动: docker compose up -d"
    echo
fi

# 创建 systemd 服务
if command -v systemctl &>/dev/null && [ -d /etc/systemd/system ]; then
    read -rp "是否安装为系统服务（开机自启）? [y/N]: " INSTALL_SERVICE
    if [[ "$INSTALL_SERVICE" =~ ^[Yy]$ ]]; then
        SERVICE_FILE="/etc/systemd/system/lingjing-manufacturing.service"
        sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=灵境制造 LNN Manufacturing AI Service
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$SCRIPT_DIR/python
Environment=PATH=$SCRIPT_DIR/venv/bin:/usr/local/bin:/usr/bin
ExecStart=$SCRIPT_DIR/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --log-level info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable lingjing-manufacturing.service
        info "系统服务已安装并设为开机自启"
        echo "  启动: sudo systemctl start lingjing-manufacturing"
        echo "  状态: sudo systemctl status lingjing-manufacturing"
    fi
fi

echo
read -rp "是否立即启动服务? [y/N]: " START_NOW
if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
    echo
    echo "正在启动服务... 按 Ctrl+C 停止"
    echo
    cd "$SCRIPT_DIR/python"
    ../venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --log-level info
fi
