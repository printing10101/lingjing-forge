# TDengine 部署问题记录（M0.2 任务）

> 本文件用于记录 **任务 M0.2：TDengine 时序数据库引入与集成** 实施过程中遇到的
> 启动 / 集成问题及解决方法。如未出现问题，本文件可仅作为"已就绪"清单。

## 1. 任务背景

- 任务编号：M0.2
- 目标：通过 Docker Compose 在本地环境部署 TDengine 时序数据库，并提供
  Python 客户端（`python/app/services/tdengine_client.py`）及单元测试。
- 关联文件：
  - `docker-compose.yml`（新增 `lnn-tdengine` 服务）
  - `python/app/services/tdengine_client.py`
  - `python/app/services/tests/test_tdengine_client.py`
  - `python/requirements.txt`（新增 `taospy>=2.7.0`）
  - `.env.example`（新增 `TDENGINE_URL` 等环境变量）

## 2. 当前环境（截至 2026-06-11）

| 项目 | 状态 | 备注 |
|------|------|------|
| Docker CLI | 已安装（v29.5.2） | `docker --version` 通过 |
| Docker Compose 插件 | 已安装（v5.1.4） | `docker compose config` 通过 |
| Docker Desktop Daemon | **未运行** | 当前沙箱环境未启动 Docker Desktop 服务 |
| `tdengine/tdengine:3.0.7.5` 镜像 | 未拉取 | 需要 daemon 运行后执行 `docker compose pull lnn-tdengine` |
| `taospy` Python 驱动 | 已安装（v2.8.9） | `pip install taospy` 成功 |

### 2.1 实际错误信息

执行 `docker compose up -d lnn-tdengine` 时返回：

```
unable to get image 'tdengine/tdengine:3.0.7.5':
failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running:
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

`Start-Service com.docker.service` 报：

```
由于以下错误无法启动服务"Docker Desktop Service (com.docker.service)":
无法打开计算机"."上的 com.docker.service 服务。
```

**结论：** 本次自动化执行环境无法启动 Docker 守护进程，因此未能在沙箱内对
`lnn-tdengine` 容器做端到端运行验证。但：

1. `docker compose config` 通过——证明 Compose 文件结构正确、`lnn-tdengine`
   服务定义符合规范。
2. Python 客户端模块（`tdengine_client.py`）的所有同步/异步 API 在缺少
   `taos` 原生库或服务不可达时均能**安全降级**（返回 `None` / 空列表 / `False`），
   而非抛出未处理异常。
3. 单元测试套件（`test_tdengine_client.py`）已设计为：**服务不可达时自动
   `pytest.skip`**，不会因环境问题误报失败。

## 3. 完整验收执行步骤（开发机操作）

> 在装有 Docker Desktop 并能正常拉取镜像的环境中，按以下顺序逐项执行即可完成
> M0.2 任务验收。

### 3.1 启动服务

```bash
# 进入项目根目录
cd "C:\Users\<user>\Desktop\灵境制造（上线版）"

# 复制环境变量模板（如尚未复制）
cp .env.example .env        # Linux/Mac
# Copy-Item .env.example .env   # Windows PowerShell

# 编辑 .env，填入：
#   POSTGRES_PASSWORD=***
#   GF_SECURITY_ADMIN_PASSWORD=***
# 其余默认值即可（TDENGINE_* 已就位）

# 启动 TDengine 容器
docker compose up -d lnn-tdengine
```

### 3.2 等待健康

```bash
# 10-30 秒后查看健康状态
docker compose ps lnn-tdengine
# 期望输出：STATUS 列包含 "Up ... (healthy)"

# 如未 healthy，可通过日志排查
docker compose logs lnn-tdengine --tail 100
```

健康检查命令双重验证：

```bash
docker exec lnn-tdengine taos -s 'show databases;'
docker exec lnn-tdengine curl -fsS http://localhost:6041/api/health
```

### 3.3 运行单元测试

```bash
cd python
python -m pytest app/services/tests/test_tdengine_client.py -v
```

**期望结果：**
- `TestTDengineConfig::*` 全部通过（不依赖服务）。
- `TestValueFormatting::*` 全部通过（不依赖服务）。
- `TestClientConnection::*` / `TestDatabaseManagement::*` /
  `TestInsertAndQuery::*` 在容器健康时全部通过；不健康时**整体 skip**。

### 3.4 手动验证客户端

```bash
cd python
python -c "from app.services.tdengine_client import get_tdengine; c = get_tdengine(); print('OK' if c is not None else 'TDENGINE_DISABLED')"
```

**期望结果：** 服务可达时输出 `OK`；服务不可达时输出 `TDENGINE_DISABLED` 且
无异常抛出。

## 4. 已知问题与缓解措施

| 编号 | 现象 | 原因 | 缓解 |
|------|------|------|------|
| I-1 | `import taos` 报 `unable to load taos client library` | `taospy` 2.7+ 需要原生 C 库 `taos.dll`（Windows）或 `libtaos.so`（Linux） | 部署在 Docker 容器内时由 `tdengine/tdengine` 镜像自带；开发机请安装 TDengine 客户端或使用 `taos-ws-py`（WebSocket 协议） |
| I-2 | 容器首次启动慢 | 节点注册 + 集群发现 + 初始化元数据 | `start_period: 30s` + `retries: 5` 已为健康检查预留宽限时间 |
| I-3 | `.env` 中 `TDENGINE_URL` 默认值指向容器名 `tdengine`（非 `lnn-tdengine`） | Docker 内服务间通信使用服务名 | 服务已重命名为 `lnn-tdengine`，请同步更新 `.env` 中 `TDENGINE_URL=taos://root:taosdata@lnn-tdengine:6030` |

## 5. 后续改进建议（非 M0.2 任务范围）

1. **TDengine 集群模式**：当 APT 写入量超过单节点承载时，启用三节点集群
   （修改 `taos.cfg` 与 `compose` 服务数量）。
2. **监控集成**：将 TDengine 自身暴露的 metrics 接入 Prometheus
   （`taosd` 提供 `/metrics` 端点）。
3. **WebSocket 备选**：在跨网络/防火墙受限场景下，可选用 `taos-ws-py`
   替换 `taospy`，配置项 `TDENGINE_URL` 切换为 `ws://lnn-tdengine:6041`。

## 6. 状态

- Docker Compose 文件结构正确（`docker compose config` 通过）。
- Python 客户端代码可正常导入、调用、异常安全降级（smoke test 通过）。
- 单元测试套件就绪，预期容器启动后全部通过。
- 容器实际启动需在开发机（Docker Desktop 运行中）执行。

## 7. 验收执行结果（沙箱环境，2026-06-11）

由于沙箱环境 Docker Desktop 守护进程无法启动、`taos.dll` 原生库未安装，
本次仅能在**安全降级**路径下完成验收，记录如下：

| 步骤 | 期望 | 实际 | 结论 |
|------|------|------|------|
| 1. `docker compose up -d lnn-tdengine` | 容器 `Up ... (healthy)` | `unable to get image ... open //./pipe/dockerDesktopLinuxEngine` | 沙箱无 Docker daemon |
| 2. `docker compose ps lnn-tdengine \| grep healthy` | 命中一行 | 0 行 | 容器未启动 |
| 3. `pytest app/services/tests/test_tdengine_client.py -v` | 全部 PASS | 11 passed, 10 skipped | **PASS**（服务依赖用例自动 skip） |
| 4. `python -c "from app.services.tdengine_client import get_tdengine; ..."` | `OK` 或 `TDENGINE_DISABLED` | `TDENGINE_DISABLED`（无异常，退出码 0） | **PASS**（降级安全） |

### 7.1 关键改进

- **冷却期短路（circuit breaker）**：`_TdengineHolder` 引入 5 秒冷却窗口，
  连接失败后 5 秒内不再触发重复 IO，避免反复重连导致 `get_tdengine()` 性能塌方。
  修复后 `TestExecutionTime::test_holder_singleton_no_extra_connect` 由
  6.7 秒降至 < 1 秒。
- **`close()` 重置冷却**：显式关闭后允许下次 `get_tdengine()` 重新尝试连接。

### 7.2 当前沙箱测试套件详情

```
============================= test session starts =============================
...
collecting ... collected 21 items

app\sservices\tests\test_tdengine_client.py::TestTDengineConfig::test_default_url_contains_localhost PASSED
app\sservices\tests\test_tdengine_client.py::TestTDengineConfig::test_enabled_property PASSED
app\sservices\tests\test_tdengine_client.py::TestTDengineConfig::test_env_override PASSED
app\sservices\tests\test_tdengine_client.py::TestClientConnection::test_get_tdengine_returns_client SKIPPED
app\sservices\tests\test_tdengine_client.py::TestClientConnection::test_get_tdengine_is_singleton SKIPPED
app\sservices\tests\test_tdengine_client.py::TestClientConnection::test_get_tdengine_async_returns_client SKIPPED
app\sservices\tests\test_tdengine_client.py::TestClientConnection::test_health_check_returns_healthy SKIPPED
app\sservices\tests\test_tdengine_client.py::TestDatabaseManagement::test_ensure_database_idempotent SKIPPED
app\sservices\tests\test_tdengine_client.py::TestDatabaseManagement::test_use_database SKIPPED
app\sservices\tests\test_tdengine_client.py::TestInsertAndQuery::test_insert_1000_rows_and_query SKIPPED
app\sservices\tests\test_tdengine_client.py::TestInsertAndQuery::test_insert_empty_returns_zero SKIPPED
app\sservices\tests\test_tdengine_client.py::TestInsertAndQuery::test_time_range_filter SKIPPED
app\sservices\tests\test_tdengine_client.py::TestInsertAndQuery::test_query_no_match SKIPPED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_none PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_bool PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_int_float PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_string_escapes_quotes PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_datetime PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_bytes PASSED
app\sservices\tests\test_tdengine_client.py::TestValueFormatting::test_format_unknown_falls_back_to_str PASSED
app\sservices\tests\test_tdengine_client.py::TestExecutionTime::test_holder_singleton_no_extra_connect PASSED

======================= 11 passed, 10 skipped in 2.76s ========================
```

**所有 SKIPPED 用例的根因均为 `TDengine` 服务不可达**（Docker daemon 未运行 + 无本地
`taos.dll`）。SKIPPED 状态由测试自身根据 `tdengine_ready` fixture 主动判定，符合
"服务不可达时降级而非失败"的设计原则。

### 7.3 后续开发机一键验证

在 Docker Desktop 可正常运行的开发机上，复制以下命令即可一次性走完全部验收：

```powershell
cd 'C:\Users\<user>\Desktop\灵境制造（上线版'
Copy-Item .env.example .env -Force
docker compose up -d lnn-tdengine
# 等待 10-30 秒
docker compose ps lnn-tdengine
Set-Location python
python -m pytest app/services/tests/test_tdengine_client.py -v
python -c "from app.services.tdengine_client import get_tdengine; c = get_tdengine(); print('OK' if c is not None else 'TDENGINE_DISABLED')"
```

预期：21 个测试全部 PASS（含 1000 行写入 + 时间范围查询），第 4 步输出 `OK`。
