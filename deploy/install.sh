#!/usr/bin/env bash
# =============================================================================
# 灵境制造 - 一键安装脚本（Linux / macOS / WSL2）
# =============================================================================
# 一条命令完成安装：零前置依赖（仅 git）、无需 sudo。
#
# 用法：
#   官方源：
#     curl -fsSL https://raw.githubusercontent.com/printing10101/Virtual-Realm-Manufacturing/main/deploy/install.sh | bash
#   中国大陆（GitHub 代理加速）：
#     LINGJING_CN=1 curl -fsSL https://raw.githubusercontent.com/printing10101/Virtual-Realm-Manufacturing/main/deploy/install.sh | bash
#   带参数：
#     curl -fsSL .../install.sh | bash -s -- --port 8765 --no-systemd
#
# 环境变量（均可覆盖默认值）：
#   LINGJING_HOME        安装根目录（默认 ~/.lingjing-manufacturing，专属目录，
#                        避免与既有 ~/.lingjing 数据冲突）
#   LINGJING_CN=1        使用中国大陆镜像（GitHub 代理 + 阿里云 PyPI）
#   LINGJING_REPO        源码仓库地址（默认 GitHub 官方仓库）
#   LINGJING_SRC         已存在的源码目录（跳过 clone，直接复用）
#   LINGJING_PYTHON_MIRROR  uv Python 安装镜像（UV_PYTHON_INSTALL_MIRROR）
#   LINGJING_PORT        API 端口（默认 8765）
#
# 幂等性：重复执行安全（已存在则跳过对应步骤）。
# =============================================================================
set -euo pipefail

# -----------------------------------------------------------------------------
# 常量与配置
# -----------------------------------------------------------------------------
DEFAULT_REPO="https://github.com/printing10101/Virtual-Realm-Manufacturing.git"
CN_REPO_PREFIX="https://gh-proxy.com/"
PYPI_CN="https://mirrors.aliyun.com/pypi/simple/"

LINGJING_HOME="${LINGJING_HOME:-$HOME/.lingjing-manufacturing}"
LINGJING_SRC="${LINGJING_SRC:-$LINGJING_HOME/lingjing}"
LINGJING_VENV="$LINGJING_HOME/venv"
LINGJING_PORT="${LINGJING_PORT:-8765}"
LINGJING_CN="${LINGJING_CN:-0}"
LINGJING_REPO="${LINGJING_REPO:-$DEFAULT_REPO}"
INSTALL_SYSTEMD="ask"   # ask | yes | no

# Windows (Git Bash / MSYS / Cygwin) 平台适配：uv 创建 Scripts/python.exe 且不认 /c/ 路径
IS_WINDOWS=0
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac
win_path() {
    # POSIX 路径 → Windows 风格（仅 Windows 下生效）
    if [ "$IS_WINDOWS" = "1" ] && command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$1"
    else
        echo "$1"
    fi
}

# -----------------------------------------------------------------------------
# 颜色与日志函数
# -----------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; NC=''
fi
info()  { echo -e "${GREEN}[√]${NC} $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; exit 1; }
step()  { echo -e "\n${CYAN}==>${NC} $*"; }

# -----------------------------------------------------------------------------
# 参数解析（curl | bash -s -- ... 场景）
# -----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --port) LINGJING_PORT="$2"; shift 2 ;;
        --no-systemd) INSTALL_SYSTEMD="no"; shift ;;
        --systemd) INSTALL_SYSTEMD="yes"; shift ;;
        --cn) LINGJING_CN=1; shift ;;
        --help|-h)
            echo "灵境制造一键安装脚本
参数：
  --port <n>        API 端口（默认 8765）
  --no-systemd      不询问 systemd 服务安装
  --systemd         直接安装 systemd 服务（需 sudo）
  --cn              使用中国大陆镜像（GitHub 代理 + 阿里云 PyPI）
  --help            显示帮助"
            exit 0 ;;
        *) shift ;;
    esac
done

# -----------------------------------------------------------------------------
# [1/7] 前置检查
# -----------------------------------------------------------------------------
step "[1/7] 检查运行环境..."
if ! command -v git >/dev/null 2>&1; then
    error "未检测到 git。请先安装：
  Ubuntu/Debian: sudo apt install git
  CentOS/RHEL:   sudo yum install git
  macOS:         brew install git"
fi

PYTHON_BIN=""
# 定位源码目录：
#   1. 当前目录是仓库（兼容「已 clone 后在仓库内运行」）
#   2. LINGJING_SRC 已存在
#   3. 否则 clone 到 LINGJING_HOME
if [ -f "$(pwd)/engineering/python/requirements.txt" ]; then
    LINGJING_SRC="$(pwd)"
    info "检测到当前目录为源码仓库，直接使用: $LINGJING_SRC"
elif [ -f "$LINGJING_SRC/engineering/python/requirements.txt" ]; then
    info "使用已有源码目录: $LINGJING_SRC"
else
    mkdir -p "$LINGJING_HOME"
    if [ "$LINGJING_CN" = "1" ] && [[ "$LINGJING_REPO" == "$DEFAULT_REPO" ]]; then
        LINGJING_REPO="${CN_REPO_PREFIX}${LINGJING_REPO}"
        warn "已启用 GitHub 代理加速（gh-proxy.com）"
    fi
    info "克隆源码仓库到 $LINGJING_SRC ..."
    git clone --depth 1 "$LINGJING_REPO" "$LINGJING_SRC" \
        || error "克隆仓库失败。中国大陆用户可加 LINGJING_CN=1 使用代理，或设置 LINGJING_REPO 指定可达镜像。"
fi

# -----------------------------------------------------------------------------
# [2/7] 安装 uv 与 Python 3.12（自动处理运行时，无需 sudo）
# -----------------------------------------------------------------------------
step "[2/7] 准备 Python 运行时（uv + Python 3.12）..."
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    info "未检测到 uv，正在安装（Astral 官方脚本）..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || error "uv 安装失败"
fi
info "uv 版本: $(uv --version)"

# 中国大陆镜像支持（可选）
if [ -n "${LINGJING_PYTHON_MIRROR:-}" ]; then
    export UV_PYTHON_INSTALL_MIRROR="$LINGJING_PYTHON_MIRROR"
    info "使用 Python 安装镜像: $LINGJING_PYTHON_MIRROR"
fi

# 安装 Python 3.12（若 venv 不存在则创建）
if [ ! -x "$LINGJING_VENV/bin/python" ] && [ ! -x "$LINGJING_VENV/Scripts/python.exe" ]; then
    info "安装 Python 3.12 并创建虚拟环境..."
    uv venv "$(win_path "$LINGJING_VENV")" --python 3.12 || {
        warn "Python 3.12 安装失败（可能是网络问题）。中国大陆用户可设置 LINGJING_PYTHON_MIRROR，例如："
        warn "  LINGJING_PYTHON_MIRROR='https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download'"
        exit 1
    }
else
    info "虚拟环境已存在，跳过创建"
fi
# Windows 的 uv venv 使用 Scripts/python.exe 布局（Windows）或 bin/python（Unix）
if [ -x "$LINGJING_VENV/Scripts/python.exe" ]; then
    PYTHON_BIN="$LINGJING_VENV/Scripts/python.exe"
else
    PYTHON_BIN="$LINGJING_VENV/bin/python"
fi
info "Python 版本: $("$PYTHON_BIN" --version)"

# -----------------------------------------------------------------------------
# [3/7] 安装 Python 依赖（国内镜像优先）
# -----------------------------------------------------------------------------
step "[3/7] 安装 Python 依赖..."
REQ_FILE_POSIX="$LINGJING_SRC/engineering/python/requirements.txt"
[ -f "$REQ_FILE_POSIX" ] || error "未找到依赖清单: $REQ_FILE_POSIX"
REQ_FILE="$(win_path "$REQ_FILE_POSIX")"

UV_PIP_ARGS=("--python" "$PYTHON_BIN")
if [ "$LINGJING_CN" = "1" ]; then
    UV_PIP_ARGS+=(--index-url "$PYPI_CN")
    info "使用阿里云 PyPI 镜像安装依赖（首次约 5-15 分钟，请耐心等待）..."
else
    info "使用官方 PyPI 安装依赖（首次约 5-15 分钟，请耐心等待）..."
fi

uv pip install "${UV_PIP_ARGS[@]}" -r "$REQ_FILE" || error "依赖安装失败，请检查网络后重试"
info "依赖安装完成"

# -----------------------------------------------------------------------------
# [4/7] 生成 .env 配置（自动生成强随机密钥）
# -----------------------------------------------------------------------------
step "[4/7] 生成 .env 配置..."
ENV_FILE="$LINGJING_SRC/.env"
if [ -f "$ENV_FILE" ]; then
    info ".env 已存在，跳过生成"
else
    [ -f "$LINGJING_SRC/.env.example" ] || error "缺少 .env.example 模板"
    cp "$LINGJING_SRC/.env.example" "$ENV_FILE"
    # 生成强随机 LNN_JWT_SECRET（≥32 字符）并回写
    "$PYTHON_BIN" - "$ENV_FILE" <<'PYEOF'
import re, secrets, sys
path = sys.argv[1]
content = open(path, encoding="utf-8").read()
jwt = secrets.token_urlsafe(48)
content = re.sub(r"(?m)^LNN_JWT_SECRET=.*$", f"LNN_JWT_SECRET={jwt}", content)
# 单机/桌面默认使用内存缓存（redis_client 未配置 REDIS_URL 时降级），
# 生产 Docker 模式由 compose env_file 注入真实 REDIS_URL 覆盖。
content = re.sub(r"(?m)^REDIS_URL=.*$", "REDIS_URL=", content)
open(path, "w", encoding="utf-8").write(content)
print(f"已生成随机 LNN_JWT_SECRET（{len(jwt)} 字符）；REDIS_URL 已置空（单机模式使用内存缓存）")
PYEOF
    warn "请检查 $ENV_FILE，按需配置 AI 模式（AI_MODE=local/cloud）与云端 API Key"
fi

# -----------------------------------------------------------------------------
# [5/7] 初始化数据库
# -----------------------------------------------------------------------------
step "[5/7] 初始化数据库..."
mkdir -p "$LINGJING_SRC/engineering/python/data"
(
    cd "$LINGJING_SRC/engineering/python"
    "$PYTHON_BIN" -c "
import asyncio
from app.database.models import init_db
asyncio.run(init_db())
print('数据库初始化完成')
" 2>/dev/null && info "数据库初始化完成" || warn "数据库初始化失败，请检查 .env 配置后运行 'lingjing doctor'"
)

# -----------------------------------------------------------------------------
# [6/7] 安装 lingjing CLI（软链到 ~/.local/bin）
# -----------------------------------------------------------------------------
step "[6/7] 安装 lingjing CLI..."
CLI_SCRIPT="$LINGJING_SRC/engineering/python/scripts/lingjing_cli.py"
[ -f "$CLI_SCRIPT" ] || error "未找到 CLI 脚本: $CLI_SCRIPT"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/lingjing" <<WRAPPER
#!/usr/bin/env bash
# 灵境制造 CLI（由 install.sh 生成）
export LINGJING_HOME="$LINGJING_HOME"
export LINGJING_SRC="$LINGJING_SRC"
export LINGJING_VENV="$LINGJING_VENV"
exec "$PYTHON_BIN" "$CLI_SCRIPT" "\$@"
WRAPPER
chmod +x "$HOME/.local/bin/lingjing"
# 确保 ~/.local/bin 在 PATH（幂等追加）
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
       [ -f "$HOME/.zshrc" ] && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" || true ;;
esac
info "lingjing CLI 已安装（运行前请先执行 source ~/.bashrc）"

# -----------------------------------------------------------------------------
# [7/7] 可选 systemd 服务
# -----------------------------------------------------------------------------
SERVICE_NAME="lingjing-manufacturing.service"
HAS_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
    HAS_SYSTEMD=1
fi

install_systemd_service() {
    step "[7/7] 配置 systemd 服务..."
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
    cat > /tmp/$SERVICE_NAME <<EOF
[Unit]
Description=灵境制造 LNN Manufacturing AI Service
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$LINGJING_SRC/engineering/python
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON_BIN -m uvicorn app.main:app --host 127.0.0.1 --port $LINGJING_PORT --log-level info
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo cp /tmp/$SERVICE_NAME "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    info "systemd 服务已安装并设为开机自启"
    echo "  启动: sudo systemctl start $SERVICE_NAME"
    echo "  状态: sudo systemctl status $SERVICE_NAME"
}

case "$INSTALL_SYSTEMD" in
    yes)
        [ "$HAS_SYSTEMD" = "1" ] || warn "未检测到 systemd，跳过服务安装（服务端也可用 lingjing start 前台运行）"
        install_systemd_service ;;
    ask)
        if [ "$HAS_SYSTEMD" = "1" ]; then
            step "[7/7] 配置 systemd 服务（可选）..."
            # 非交互（管道/自动化）场景 read 会立即 EOF，视为不安装
            YN="n"
            read -rp "是否安装为系统服务（开机自启，需 sudo）? [y/N]: " YN || YN="n"
            case "${YN:-n}" in
                y|Y) install_systemd_service ;;
                *)   info "跳过 systemd 服务安装（可用 lingjing start 启动）" ;;
            esac
        fi
        ;;
    no) : ;;
esac

# -----------------------------------------------------------------------------
# 完成
# -----------------------------------------------------------------------------
echo
echo "========================================"
echo " 灵境制造安装完成！"
echo "========================================"
echo
echo " 安装目录:    $LINGJING_HOME"
echo " 源码目录:    $LINGJING_SRC"
echo " 服务地址:    http://localhost:$LINGJING_PORT"
echo " API 文档:    http://localhost:$LINGJING_PORT/docs"
echo
echo " 下一步："
echo "   1. source ~/.bashrc      # 刷新 PATH"
echo "   2. lingjing doctor       # 检查安装状态"
echo "   3. lingjing start        # 启动服务"
echo "   4. 浏览器访问 http://localhost:$LINGJING_PORT"
echo
echo " 其他命令: lingjing stop / restart / update / uninstall"
echo
