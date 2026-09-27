# 灵境制造 - 离线部署指南

## 概述

本目录包含灵境制造系统的离线部署工具链，适用于**无互联网连接**或**网络受限**的目标环境。

## 文件说明

| 文件 | 说明 |
|------|------|
| `prepare_offline.bat` | Windows 离线包准备脚本（在有网络的机器上运行） |
| `prepare_offline.sh` | Linux 离线包准备脚本（在有网络的机器上运行） |
| `install_offline.bat` | Windows 离线安装脚本（在目标机器上运行） |
| `install_offline.sh` | Linux 离线安装脚本（在目标机器上运行） |

## 快速开始

### 第一步：准备离线包（在有网络的机器上）

#### Windows

```bat
cd deploy\offline
prepare_offline.bat
```

脚本将自动：
1. 下载所有 Python 依赖包（wheel 格式）到 `wheels/` 目录
2. 尝试构建并保存 Docker 镜像
3. 复制项目源代码和配置文件
4. 可选打包为 zip 文件

#### Linux / macOS

```bash
cd deploy/offline
chmod +x prepare_offline.sh
./prepare_offline.sh
```

### 第二步：传输到目标机器

将生成的离线包通过 U 盘、移动硬盘或内网传输工具复制到目标机器。

- Windows: `lingjing_offline_package.zip`
- Linux: `lingjing_offline_package.tar.gz`

### 第三步：在目标机器上安装

#### Windows

```bat
:: 解压 zip 文件后进入目录
cd lingjing_offline_package
install_offline.bat
```

#### Linux

```bash
tar -xzf lingjing_offline_package.tar.gz
cd offline_package
chmod +x install_offline.sh
./install_offline.sh
```

## 安装流程说明

离线安装脚本将执行以下步骤：

1. **检查 Python 环境** — 验证 Python 3.10+ 是否已安装
2. **创建虚拟环境** — 隔离项目依赖
3. **离线安装依赖** — 从 `wheels/` 目录安装（`--no-index --find-links`）
4. **加载 Docker 镜像** — 如果离线包包含 Docker 镜像
5. **初始化数据库** — 创建数据表结构
6. **启动服务** — 可选立即启动

## 系统要求

### 必选

- **Python 3.10+**（需预先安装）
- **操作系统**：Windows 10/11、Ubuntu 20.04+、CentOS 7+、macOS 12+

### 可选

- **Docker 20.10+**（如使用容器化部署）
- **Docker Compose v2+**（如使用 docker compose 启动）

## 部署模式

### 模式一：直接运行（推荐入门）

安装完成后直接运行：

```bash
# Linux/macOS
cd python && ../venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765

# Windows
cd python && ..\venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

### 模式二：Docker Compose

如果离线包包含 Docker 镜像：

```bash
# 复制并编辑环境变量
cp .env.example .env
# 编辑 .env 设置密码和密钥

# 启动全部服务
docker compose up -d

# 或仅启动 API（使用 SQLite，无需外部数据库）
docker compose -f docker-compose-sqlite.yml up -d
```

### 模式三：系统服务（Linux）

安装时选择注册为 systemd 服务后：

```bash
sudo systemctl start lingjing-manufacturing    # 启动
sudo systemctl stop lingjing-manufacturing     # 停止
sudo systemctl status lingjing-manufacturing   # 查看状态
journalctl -u lingjing-manufacturing -f        # 查看日志
```

## 常见问题

### Q: 离线安装时提示缺少依赖包？

A: 确保准备离线包时网络正常，所有依赖都已下载到 `wheels/` 目录。可以重新运行 `prepare_offline` 脚本补全。

### Q: 目标机器没有 Python 怎么办？

A: 需要预先在目标机器安装 Python 3.10+。可从 Python 官网下载安装包，或使用 Anaconda/Miniconda 离线安装包。

### Q: 如何更新离线包？

A: 在有网络的机器上重新运行 `prepare_offline` 脚本，将生成新的离线包。

### Q: Docker 镜像加载失败？

A: 确认目标机器已安装 Docker，且版本与构建时一致。可通过 `docker --version` 检查。

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| API 服务 | 8765 | 主 API 接口 |
| API 文档 | 8765/docs | Swagger UI |
| Grafana | 3000 | 监控面板（Docker 模式） |
| Prometheus | 9090 | 指标采集（Docker 模式） |
