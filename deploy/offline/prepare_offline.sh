#!/usr/bin/env bash
# =============================================================================
# 灵境制造 - Linux 离线包准备脚本
# =============================================================================
# P1-6/7/8 修复要点：
#   1. pip 源通过 PIP_INDEX_URL / PIP_TRUSTED_HOST 环境变量覆盖（默认仍用阿里云）
#   2. 依赖镜像列表与 docker-compose.yml 保持一致（redis:7.4.2-alpine、
#      postgres:15.10-alpine、tdengine/tdengine:3.0.7.5）
#   3. 配置文件复制改为条件复制，缺失文件打印跳过提示而非静默成功
#   4. 加入 --platform / --python-version / --only-binary 参数，与 .bat 对齐
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[√]${NC} $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC} $*"; }
error() { echo -e "${RED}[错误]${NC} $*"; exit 1; }

echo "========================================"
echo " 灵境制造 - Linux 离线包准备脚本"
echo "========================================"
echo

# 获取项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# P1-6 修复：pip 源可通过环境变量覆盖，默认仍为阿里云（国内开发体验）
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
# P1-7 修复：目标平台与 Python 版本（与 prepare_offline.bat 对齐）
PIP_PLATFORM="${PIP_PLATFORM:-}"
PIP_PYTHON_VERSION="${PIP_PYTHON_VERSION:-3.11}"

# 检查 Python
echo "[1/5] 检查 Python 环境..."
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    error "未检测到 Python"
fi
info "Python 环境正常: $($PYTHON_CMD --version)"

# 创建离线包目录
echo
echo "[2/5] 创建离线包目录结构..."
OFFLINE_DIR="$PROJECT_ROOT/deploy/offline/offline_package"
rm -rf "$OFFLINE_DIR"
mkdir -p "$OFFLINE_DIR"/{wheels,python,config,scripts,nginx}
info "目录结构创建完成"

# 下载 pip 依赖
echo
echo "[3/5] 下载 Python 依赖包（wheel 格式）..."
echo " 这可能需要几分钟，请耐心等待..."
echo " pip 源: $PIP_INDEX_URL"
echo " 目标平台: ${PIP_PLATFORM:-当前平台}, Python 版本: $PIP_PYTHON_VERSION"

# P1-6 修复：构建 pip download 参数列表，支持跨平台与仅二进制
PIP_DOWNLOAD_ARGS=(
    -r requirements.txt
    -d "$OFFLINE_DIR/wheels"
    -i "$PIP_INDEX_URL"
    --trusted-host "$PIP_TRUSTED_HOST"
)
if [[ -n "$PIP_PLATFORM" ]]; then
    PIP_DOWNLOAD_ARGS+=(--platform "$PIP_PLATFORM" --python-version "$PIP_PYTHON_VERSION" --only-binary=:all:)
fi

# 先尝试仅二进制下载（更快、更可靠），失败则退回允许源码编译
pip download "${PIP_DOWNLOAD_ARGS[@]}" 2>/dev/null || {
    warn "部分包无对应平台 wheel，尝试允许源码编译下载..."
    # 移除 --only-binary 参数后再试一次
    local_args=()
    for arg in "${PIP_DOWNLOAD_ARGS[@]}"; do
        case "$arg" in
            --only-binary=*) ;;
            --python-version=*) ;;
            --platform=*) ;;
            *) local_args+=("$arg") ;;
        esac
    done
    pip download "${local_args[@]}" --no-binary=:all: 2>/dev/null || {
        warn "仍有部分依赖下载失败，请检查网络或手动补全 wheels 目录"
    }
}
info "依赖包下载完成"

# 下载 Docker 镜像（如果 Docker 可用）
echo
echo "[4/5] 保存 Docker 镜像..."
if command -v docker &>/dev/null; then
    # P1-7 修复：预先创建镜像输出目录，避免 build 失败时依赖镜像保存失败
    mkdir -p "$OFFLINE_DIR/docker_images"

    # 构建镜像
    warn "尝试构建 Docker 镜像（可能需要较长时间）..."
    docker build -t lingjing-manufacturing:latest . 2>/dev/null && {
        docker save lingjing-manufacturing:latest -o "$OFFLINE_DIR/docker_images/lingjing-manufacturing.tar"
        info "Docker 镜像已保存"
    } || {
        warn "Docker 镜像构建失败，跳过（离线部署时将使用直接运行模式）"
    }

    # P1-7 修复：依赖镜像列表与 docker-compose.yml 完全对齐
    #   - redis:7.4.2-alpine（原为 redis:7-alpine，版本不匹配）
    #   - postgres:15.10-alpine（原为 postgres:16-alpine，主版本不匹配）
    #   - tdengine/tdengine:3.0.7.5（原缺失，导致离线部署 TDengine 服务无法启动）
    for img in redis:7.4.2-alpine postgres:15.10-alpine tdengine/tdengine:3.0.7.5; do
        if docker image inspect "$img" &>/dev/null; then
            SAFE_NAME=$(echo "$img" | tr '/:' '_')
            docker save "$img" -o "$OFFLINE_DIR/docker_images/${SAFE_NAME}.tar"
            info "镜像 $img 已保存"
        else
            # 自动拉取缺失镜像，避免离线包缺失依赖
            warn "镜像 $img 本地不存在，尝试拉取..."
            if docker pull "$img"; then
                SAFE_NAME=$(echo "$img" | tr '/:' '_')
                docker save "$img" -o "$OFFLINE_DIR/docker_images/${SAFE_NAME}.tar"
                info "镜像 $img 拉取并保存成功"
            else
                warn "镜像 $img 拉取失败，离线部署时需手动准备"
            fi
        fi
    done
else
    warn "未检测到 Docker，跳过镜像保存"
fi

# 复制项目文件
echo
echo "[5/5] 复制项目文件..."
# P0-2 修复：阶段2解耦后后端代码位于 engineering/python（原 python/ 已不存在）
cp -r "$PROJECT_ROOT/engineering/python/app" "$OFFLINE_DIR/python/"
# P0-2 修复：离线包内 requirements.txt 必须为真实依赖清单（根目录薄包装
# 引用 engineering/python/requirements.txt，在离线包内无法解析）
cp "$PROJECT_ROOT/engineering/python/requirements.txt" "$OFFLINE_DIR/requirements.txt"

# P1-8 修复：配置文件条件复制，缺失文件明确提示，避免静默失败造成"已复制"假象
copy_optional() {
    local src="$1"
    local dest_dir="$2"
    local label="${3:-$(basename "$src")}"
    if [[ -f "$src" ]]; then
        cp "$src" "$dest_dir/"
        info "已复制: $label"
    else
        warn "跳过（文件不存在）: $label -> $src"
    fi
}

copy_optional "$PROJECT_ROOT/.env.example" "$OFFLINE_DIR" ".env.example"
copy_optional "$PROJECT_ROOT/docker-compose.yml" "$OFFLINE_DIR" "docker-compose.yml"
copy_optional "$PROJECT_ROOT/docker-compose-cn.yml" "$OFFLINE_DIR" "docker-compose-cn.yml"
copy_optional "$PROJECT_ROOT/docker-compose-sqlite.yml" "$OFFLINE_DIR" "docker-compose-sqlite.yml"

# config 与 nginx 目录使用通配符复制，无文件时给出提示
if [[ -d "$PROJECT_ROOT/config" ]] && [[ -n "$(ls -A "$PROJECT_ROOT/config/" 2>/dev/null)" ]]; then
    cp -r "$PROJECT_ROOT/config/"* "$OFFLINE_DIR/config/" 2>/dev/null || true
    info "已复制: config/ 目录"
else
    warn "跳过（目录为空或不存在）: config/"
fi

if [[ -d "$PROJECT_ROOT/deploy/nginx" ]] && [[ -n "$(ls -A "$PROJECT_ROOT/deploy/nginx/" 2>/dev/null)" ]]; then
    cp -r "$PROJECT_ROOT/deploy/nginx/"* "$OFFLINE_DIR/nginx/" 2>/dev/null || true
    info "已复制: deploy/nginx/ 目录"
else
    warn "跳过（目录为空或不存在）: deploy/nginx/"
fi

# 复制离线安装脚本
cp "$SCRIPT_DIR/install_offline.sh" "$OFFLINE_DIR/"
cp "$SCRIPT_DIR/install_offline.bat" "$OFFLINE_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/README.md" "$OFFLINE_DIR/" 2>/dev/null || true

# 设置可执行权限
chmod +x "$OFFLINE_DIR/install_offline.sh" 2>/dev/null || true

info "项目文件复制完成"

# 打包
echo
echo "========================================"
echo " 离线包准备完成！"
echo "========================================"
echo
echo " 离线包位置: $OFFLINE_DIR"
echo
echo " 下一步操作："
echo "   1. 打包: tar -czf lingjing_offline_package.tar.gz -C $(dirname $OFFLINE_DIR) $(basename $OFFLINE_DIR)"
echo "   2. 传输到目标机器"
echo "   3. 解压: tar -xzf lingjing_offline_package.tar.gz"
echo "   4. 运行: cd offline_package && bash install_offline.sh"
echo

read -rp "是否现在打包? [y/N]: " ZIP_NOW
if [[ "$ZIP_NOW" =~ ^[Yy]$ ]]; then
    echo "正在打包..."
    tar -czf "$PROJECT_ROOT/deploy/offline/lingjing_offline_package.tar.gz" \
        -C "$(dirname "$OFFLINE_DIR")" "$(basename "$OFFLINE_DIR")"
    info "打包完成: deploy/offline/lingjing_offline_package.tar.gz"
fi
