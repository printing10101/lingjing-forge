# =============================================================================
# Multi-stage Dockerfile for LNN Manufacturing AI Service
# 镜像源可参数化（P0-13 修复）：默认使用国内镜像加速，海外/离线部署可通过 build-arg 覆盖
#   docker build \
#     --build-arg BASE_REGISTRY=docker.io/library \
#     --build-arg PIP_INDEX_URL=https://pypi.org/simple/ \
#     --build-arg PIP_TRUSTED_HOST= \
#     -t lnn-api .
# =============================================================================

# ---- Stage 1: Builder - 安装构建依赖和编译 Python 包 ----
# P0-13 修复：基础镜像改为可参数化，默认国内镜像，海外部署可覆盖为 docker.io/library
ARG BASE_REGISTRY=swr.cn-north-4.myhuaweicloud.com/library
FROM ${BASE_REGISTRY}/python:3.12-slim AS builder

# P0-13 修复：pip 源可参数化，默认阿里云镜像，海外部署可通过 --build-arg 覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /build

# 配置 pip 镜像源（使用 build-arg 注入的值，海外部署可覆盖）
RUN mkdir -p /etc/pip && \
    echo "[global]" > /etc/pip/pip.conf && \
    echo "index-url = ${PIP_INDEX_URL}" >> /etc/pip/pip.conf && \
    echo "[install]" >> /etc/pip/pip.conf && \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then \
        echo "trusted-host=${PIP_TRUSTED_HOST}" >> /etc/pip/pip.conf; \
    fi

# 安装构建工具（仅在此阶段需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，充分利用 Docker 缓存层
COPY engineering/python/requirements.txt ./requirements.txt

# 安装 Python 依赖到独立目录（pip 源由 PIP_INDEX_URL 环境变量控制）
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 复制源代码
COPY . .

# ---- Stage 2: Runtime - 最小化运行时镜像 ----
# P0-13 修复：runtime 阶段也需要使用同一 BASE_REGISTRY ARG
ARG BASE_REGISTRY=swr.cn-north-4.myhuaweicloud.com/library
FROM ${BASE_REGISTRY}/python:3.12-slim AS runtime

# P0-3 修复：支持构建版本号注入（release.yml 通过 build-arg 传入，应用可经环境变量读取）
ARG BUILD_VERSION=dev
ENV BUILD_VERSION=${BUILD_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_HOME=/app \
    APP_ENV=production \
    PORT=8765 \
    PYTHONPATH=/app/python

WORKDIR $APP_HOME

# 仅安装运行时依赖（最小化镜像体积）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    libspatialindex-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# 从 builder 阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 复制应用代码（仅复制必要的目录）
# 注意：源码实际位于 engineering/python/ 下，需保持路径一致
COPY --from=builder /build/engineering/python/app ./python/app
COPY --from=builder /build/engineering/python/alembic ./python/alembic
COPY --from=builder /build/engineering/python/alembic.ini ./python/
COPY --from=builder /build/engineering/python/config ./python/config
COPY --from=builder /build/config ./config

# 创建非 root 用户并设置权限
RUN groupadd -r appuser && \
    useradd -r -g appuser -d $APP_HOME -s /sbin/nologin appuser && \
    chown -R appuser:appuser $APP_HOME

USER appuser

EXPOSE 8765

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8765/api/health/ping || exit 1

# 启动命令（容器内绑定 0.0.0.0 是必需的，外部端口绑定由 docker-compose 的 ports 映射控制）
# 外部访问限制在 docker-compose.yml 中通过 127.0.0.1:8765:8765 实现，不会直接对外暴露
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "4"]
