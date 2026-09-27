# 灵境制造 - 部署配置

本目录包含灵境制造项目生产环境部署所需的所有配置文件，支持 Docker Compose、Kubernetes、裸机三种部署模式。各子目录分别承载容器编排、监控可观测性、反向代理、时序数据库初始化以及离线安装等场景的配置与脚本。

## 目录结构

```
deploy/
├── grafana/          # Grafana 监控仪表盘与数据源配置
├── k8s/              # Kubernetes 部署清单（deployment/hpa/network-policy/pdb/secret）
├── nginx/            # Nginx 反向代理配置（已有 README_TLS.md）
├── offline/          # 离线安装脚本（已有 README.md）
├── prometheus/       # Prometheus 监控与告警规则
├── tdengine/         # TDengine 时序数据库初始化 SQL
├── install.bat       # Windows 一键安装脚本
└── install.sh        # Linux/macOS 一键安装脚本
```

| 子目录/文件 | 用途 |
| --- | --- |
| `grafana/` | Grafana 监控仪表盘 JSON 与数据源 provisioning 配置 |
| `k8s/` | Kubernetes 部署所需的 Deployment、HPA、NetworkPolicy、PDB、Secret 清单 |
| `nginx/` | Nginx 反向代理与 TLS 终止配置 |
| `offline/` | 离线场景下使用的安装与准备脚本 |
| `prometheus/` | Prometheus 抓取配置与告警规则 |
| `tdengine/` | TDengine 时序数据库初始化 SQL 脚本 |
| `install.bat` | Windows 平台一键安装脚本 |
| `install.sh` | Linux/macOS 平台一键安装脚本 |

## 部署模式

### 3.1 Docker Compose 部署（推荐）

使用项目根目录的 `docker-compose.yml` 进行部署，适用于大多数生产与预发布场景。

- 启动服务：

```bash
docker compose up -d
```

- 健康检查：

```bash
curl http://localhost:8765/health
```

- 查看日志：

```bash
docker compose logs -f
```

### 3.2 Kubernetes 部署

前置条件：已安装 `kubectl` 并配置好目标集群的访问凭证。

部署步骤：

1. 创建命名空间：

```bash
kubectl create namespace lingjing
```

2. 创建 Secret（先修改 `deploy/k8s/secret.example.yml` 中的敏感值）：

```bash
kubectl apply -f deploy/k8s/secret.example.yml
```

3. 部署应用：

```bash
kubectl apply -f deploy/k8s/deployment.yml
```

4. 配置自动伸缩：

```bash
kubectl apply -f deploy/k8s/hpa.yml
```

5. 配置网络策略：

```bash
kubectl apply -f deploy/k8s/network-policy.yml
```

6. 配置 PodDisruptionBudget（PDB）：

```bash
kubectl apply -f deploy/k8s/pdb.yml
```

验证部署：

```bash
kubectl get pods -n lingjing
```

### 3.3 裸机部署（开发/测试）

适用于开发、测试或无容器化环境的部署场景。

- Windows：

```bash
deploy\install.bat
```

- Linux/macOS：

```bash
./deploy/install.sh
```

- 离线安装请参考 `deploy/offline/README.md`。

## 监控与可观测性

### 4.1 Prometheus

- 配置文件：`deploy/prometheus/prometheus.yml`
- 告警规则：`deploy/prometheus/alert_rules.yml`

Prometheus 负责采集应用与基础设施指标，并基于 `alert_rules.yml` 中定义的规则触发告警。

### 4.2 Grafana

- 仪表盘 provisioning 配置：`deploy/grafana/provisioning/dashboards/lnn-dashboard.yml`
- 仪表盘 JSON：`deploy/grafana/dashboards/flywheel.json`
- 数据源配置：`deploy/grafana/provisioning/datasources/prometheus.yml`

Grafana 通过 provisioning 机制自动加载仪表盘与数据源，无需手动导入。

## Nginx 反向代理

- 配置文件：`deploy/nginx/nginx.conf`
- TLS 配置：参考 `deploy/nginx/README_TLS.md`

Nginx 作为入口反向代理，负责请求转发、TLS 终止与基础安全头注入。

## TDengine 时序数据库

- 初始化 SQL：`deploy/tdengine/init.sql`

该脚本用于创建 TDengine 所需的数据库、超级表与子表结构，部署时序数据相关服务前需先执行。

## 环境变量

以下为关键环境变量（完整列表参考项目根目录 `.env.example`）：

| 环境变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 连接字符串 |
| `JWT_SECRET` | JWT 签名密钥（生产环境必须修改） |
| `LNN_MODEL_PATH` | LNN 模型文件路径 |
| `OLLAMA_BASE_URL` | Ollama 服务地址 |
| `VLLM_BASE_URL` | vLLM 服务地址 |

## 安全注意事项

- 生产环境必须修改所有默认密钥（`JWT_SECRET`、数据库密码等）。
- K8s Secret 应使用外部密钥管理方案，如 Sealed Secrets 或 External Secrets Operator，避免将明文敏感信息提交至版本库。
- Nginx 必须启用 TLS 1.3，配置方式参考 `deploy/nginx/README_TLS.md`。
- 容器镜像已固定到 patch 版本（如 `redis:7.4.2-alpine`），避免因浮动标签引入未经验证的变更。
