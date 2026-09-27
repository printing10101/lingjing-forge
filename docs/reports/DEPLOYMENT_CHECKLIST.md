# 灵境制造系统 - 工厂部署检查清单

## 已完成项

### 1. 核心功能完整性
- [x] LNN 推理引擎完整实现（CFC/LTC/Hybrid 模型）
- [x] OPC UA 工业协议适配器（支持订阅模式、自动节点发现）
- [x] MES/ERP 集成客户端（工单同步、生产数据上报、质量数据上传）
- [x] TDengine 时序数据库集成（高频传感器数据存储）
- [x] FastAPI 后端服务（RESTful API、WebSocket 支持）
- [x] 前端构建产物完整（dist/ 目录包含所有页面和组件）

### 2. 生产部署配置
- [x] Docker 多阶段构建（优化镜像体积）
- [x] Docker Compose 完整服务栈（API、Redis、PostgreSQL、TDengine、Prometheus、Grafana、Nginx）
- [x] 健康检查配置（所有服务均配置 healthcheck）
- [x] 资源限制配置（CPU/内存限制，防止资源耗尽）
- [x] 自动重启策略（`restart: unless-stopped`）
- [x] 数据库迁移脚本（Alembic）
- [x] TDengine 初始化脚本（`deploy/tdengine/init.sql`）

### 3. 安全配置
- [x] CORS 安全验证（启动时强制校验）
- [x] JWT 认证机制
- [x] 环境变量管理（.env 文件，敏感信息不硬编码）
- [x] 非 root 用户运行（Docker 容器内使用 appuser）
- [x] Redis 强制密码认证
- [x] PostgreSQL 端口不对外暴露
- [x] TDengine 端口不对外暴露
- [x] Nginx HTTPS 强制重定向
- [x] TLS 安全配置（TLS 1.2/1.3，强加密套件）
- [x] HSTS 头配置

### 4. 监控与日志
- [x] Prometheus 监控指标
- [x] Grafana 可视化仪表板
- [x] 结构化日志记录（logger 替代 print）
- [x] 错误追踪和异常处理
- [x] 健康检查端点（`/api/health/ping`）

### 5. 工业集成
- [x] OPC UA 自动重连机制
- [x] OPC UA 连接健康检查
- [x] 数据缓冲和批量持久化
- [x] CNC 节点自动发现
- [x] 指数退避重试策略

### 6. 打包与分发
- [x] PyInstaller spec 文件配置（`python/lingjing-backend.spec`）
- [x] Hidden imports 完整配置（LNN 模块、数据库驱动、OPC UA）
- [x] Tauri 桌面应用构建脚本（`build.py`）
- [x] 前端构建产物路径配置

---

## 部署前必须完成

### 1. TLS 证书配置（必需）

**问题**：Nginx HTTPS 配置需要 TLS 证书，但 `deploy/nginx/certs/` 目录为空。

**解决方案**：

#### 方式一：使用 Let's Encrypt（推荐，免费）
```bash
# 安装 Certbot
sudo apt update
sudo apt install certbot python3-certbot-nginx

# 获取证书（确保域名已解析到服务器 IP）
sudo certbot --nginx -d your-domain.com

# 复制到项目目录
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem deploy/nginx/certs/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem deploy/nginx/certs/

# 设置权限
sudo chown root:root deploy/nginx/certs/*.pem
sudo chmod 644 deploy/nginx/certs/fullchain.pem
sudo chmod 600 deploy/nginx/certs/privkey.pem
```

#### 方式二：使用阿里云 SSL 证书
1. 登录阿里云控制台
2. 申请 SSL 证书（免费或付费）
3. 下载 Nginx 格式证书
4. 将证书文件放入 `deploy/nginx/certs/`：
   - `your-domain.pem` → `fullchain.pem`
   - `your-domain.key` → `privkey.pem`

#### 方式三：自签名证书（仅用于测试）
```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout deploy/nginx/certs/privkey.pem \
  -out deploy/nginx/certs/fullchain.pem \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=YourCompany/CN=your-domain.com"
```

**验证**：
```bash
openssl x509 -in deploy/nginx/certs/fullchain.pem -text -noout
```

**详细文档**：参见 `deploy/nginx/README_TLS.md`

---

### 2. 环境变量配置（必需）

**问题**：`.env` 文件中的密码和密钥需要替换为生产环境的真实值。

**检查清单**：
```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env 文件，替换以下关键变量：
# - POSTGRES_PASSWORD（至少 32 位强密码）
# - REDIS_PASSWORD（至少 16 位）
# - LNN_JWT_SECRET（使用以下命令生成）
# - GF_SECURITY_ADMIN_PASSWORD（至少 12 位）
# - TDENGINE_PASSWORD

# 3. 生成强密钥
python -c "import secrets; print(secrets.token_urlsafe(64))"

# 4. 确保 DB_URL 和 REDIS_URL 中的密码与对应变量一致
```

**安全提示**：
- 所有密码使用密码管理器生成的随机字符串
- `.env` 文件已在 `.gitignore` 中，不会提交到 Git
- 生产环境不要使用默认密码

---

### 3. OPC UA 配置（工厂环境）

**问题**：OPC UA 端点地址需要根据实际机床配置修改。

**配置方式**：

#### 方式一：环境变量（推荐）
在 `.env` 文件中添加：
```bash
OPCUA_ENDPOINT=opc.tcp://192.168.1.100:4840
OPCUA_TIMEOUT=10.0
OPCUA_INTERVAL=1.0
```

#### 方式二：配置文件
编辑 `config/opcua_config.yaml`：
```yaml
endpoint: "opc.tcp://192.168.1.100:4840"
timeout: 10.0
interval: 1.0
batch_size: 10
```

**测试连接**：
```bash
# 使用 UaExpert 或其他 OPC UA 客户端测试连接
# 确保机床 OPC UA 服务器已启动且网络可达
```

---

### 4. MES 系统配置（可选）

**问题**：如果需要与 MES/ERP 系统集成，需要配置 MES 客户端。

**配置方式**：
在 `.env` 文件中添加：
```bash
MES_BASE_URL=https://mes.your-company.com
MES_API_KEY=your-api-key
MES_TIMEOUT=30.0
```

**测试连接**：
```python
from app.integrations.mes.client import MESClient

async def test():
    async with MESClient("https://mes.example.com", "api-key") as client:
        healthy = await client.health_check()
        print(f"MES 系统状态: {healthy}")
```

---

### 5. 数据库初始化（首次部署）

**问题**：TDengine 数据库表结构需要初始化。

**解决方案**：
1. 启动 TDengine 容器：
   ```bash
   docker compose up -d lnn-tdengine
   ```

2. 等待 TDengine 完全启动（约 30 秒）

3. 执行初始化脚本：
   ```bash
   docker exec -i lnn-tdengine taos < deploy/tdengine/init.sql
   ```

4. 验证数据库创建：
   ```bash
   docker exec -it lnn-tdengine taos -s "SHOW DATABASES;"
   docker exec -it lnn-tdengine taos -s "USE lnn_tsdb; SHOW STABLES;"
   ```

**注意**：Docker Compose 已配置自动挂载初始化脚本到 `/docker-entrypoint-initdb.d/init.sql`，首次启动时会自动执行。

---

## 部署步骤

### 1. 准备服务器

**系统要求**：
- CPU: 至少 4 核（推荐 8 核）
- 内存: 至少 8GB（推荐 16GB）
- 存储: 至少 50GB SSD
- 操作系统: Ubuntu 20.04/22.04 LTS 或 CentOS 7/8

**安装 Docker**：
```bash
# Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 验证安装
docker --version
docker compose version
```

### 2. 部署代码

```bash
# 克隆代码
git clone <your-repo-url>
cd 灵境制造（上线版）

# 配置环境变量
cp .env.example .env
nano .env  # 编辑环境变量

# 配置 TLS 证书
# 参见上方"TLS 证书配置"章节

# 构建镜像
docker compose build

# 启动服务
docker compose --profile full up -d

# 查看日志
docker compose logs -f lnn-api
```

### 3. 验证部署

```bash
# 检查服务状态
docker compose ps

# 检查健康状态
curl http://localhost:8765/api/health/ping

# 检查 HTTPS
curl -I https://your-domain.com

# 检查 Grafana
open http://your-server-ip:3000
```

### 4. 前端部署（Tauri 桌面应用）

**方式一：使用预构建版本**
```bash
# 下载预构建的桌面应用安装包
# Windows: LingjingSetup-x64.exe
# macOS: Lingjing-x64.dmg
# Linux: Lingjing-x64.appimage
```

**方式二：从源码构建**
```bash
# 安装依赖
npm install

# 构建 Python 后端 Sidecar
python python/scripts/build_backend.py

# 构建前端
npm run build

# 构建 Tauri 桌面应用
npm run tauri build
```

---

## 故障排查

### 1. API 服务无法启动

**症状**：`docker compose logs lnn-api` 显示错误

**排查步骤**：
```bash
# 检查数据库连接
docker exec -it lnn-postgres psql -U lnn -d lnn_db -c "SELECT 1;"

# 检查 Redis 连接
docker exec -it lnn-redis redis-cli -a <password> ping

# 检查 TDengine 连接
docker exec -it lnn-tdengine taos -s "SHOW DATABASES;"

# 查看详细日志
docker compose logs -f lnn-api | grep ERROR
```

### 2. OPC UA 连接失败

**症状**：日志显示 "Failed to connect to OPC UA server"

**排查步骤**：
```bash
# 1. 检查网络连通性
ping <opcua-server-ip>
telnet <opcua-server-ip> 4840

# 2. 检查 OPC UA 服务器是否启动
# 使用 UaExpert 客户端测试连接

# 3. 检查防火墙
sudo ufw allow 4840/tcp

# 4. 检查环境变量
docker exec -it lnn-api env | grep OPCUA
```

### 3. TLS 证书错误

**症状**：Nginx 启动失败，日志显示 "SSL_CTX_use_certificate" 错误

**排查步骤**：
```bash
# 检查证书文件是否存在
ls -la deploy/nginx/certs/

# 检查证书格式
openssl x509 -in deploy/nginx/certs/fullchain.pem -text -noout

# 检查证书和私钥是否匹配
openssl x509 -noout -modulus -in deploy/nginx/certs/fullchain.pem | openssl md5
openssl rsa -noout -modulus -in deploy/nginx/certs/privkey.pem | openssl md5

# 检查文件权限
ls -la deploy/nginx/certs/*.pem
```

### 4. 数据库迁移失败

**症状**：API 启动时报 "table does not exist" 错误

**解决方案**：
```bash
# 手动执行数据库迁移
docker exec -it lnn-api alembic upgrade head

# 检查迁移状态
docker exec -it lnn-api alembic current
```

---

## 性能优化

### 1. 数据库优化

**PostgreSQL**：
```bash
# 调整连接池大小（在 .env 中）
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10

# 定期执行 VACUUM
docker exec -it lnn-postgres psql -U lnn -d lnn_db -c "VACUUM ANALYZE;"
```

**TDengine**：
```bash
# 调整数据保留策略
docker exec -it lnn-tdengine taos -s "ALTER DATABASE lnn_tsdb KEEP 365;"

# 定期清理旧数据
docker exec -it lnn-tdengine taos -s "DELETE FROM lnn_tsdb.opcua_data WHERE ts < NOW() - 365d;"
```

### 2. 缓存优化

**Redis**：
```bash
# 调整内存限制（在 docker-compose.yml 中）
--maxmemory 512mb
--maxmemory-policy allkeys-lru
```

### 3. API 性能

**调整 workers 数量**（在 Dockerfile 中）：
```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "8"]
```

---

## 安全加固

### 1. 网络安全
- [ ] 配置防火墙规则，仅开放必要端口（80, 443）
- [ ] 使用 VPN 或专线连接工厂网络
- [ ] 禁用不必要的服务端口

### 2. 应用安全
- [ ] 定期更新依赖包（`pip audit`）
- [ ] 启用 Rate Limiting（防止 API 滥用）
- [ ] 配置 CORS 白名单（限制跨域访问）

### 3. 数据安全
- [ ] 定期备份数据库（已配置自动备份）
- [ ] 加密敏感数据（API 密钥、密码）
- [ ] 审计日志记录（关键操作）

---

## 技术支持

**文档资源**：
- 项目文档：`docs-site/`
- API 文档：`http://localhost:8765/docs`（Swagger UI）
- TLS 配置：`deploy/nginx/README_TLS.md`

**常见问题**：
- 参见本文件"故障排查"章节
- 参见 `docs/` 目录下的详细文档

---

## 部署完成检查

- [ ] TLS 证书已配置且有效
- [ ] 环境变量已替换为生产值
- [ ] 所有服务健康检查通过
- [ ] OPC UA 连接测试成功
- [ ] 数据库初始化完成
- [ ] 前端应用可以正常访问
- [ ] 监控仪表板可以正常访问
- [ ] 日志记录正常

**部署完成！** 
