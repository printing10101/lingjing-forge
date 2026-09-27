# MCP Agent Gateway 工具面使用说明

> 适用版本：`v2.8.0`（26 工具面口径，2026-09-17 程序化枚举核验）
> 维护模块：`mcp_server/`
> 单一事实源：本文工具清单由注册代码枚举生成（见 §5 验证脚本），改工具面先改代码、再更新本文。

## 1. 定位与架构

MCP（Model Context Protocol）网关让**厂里已有的 AI Agent / 编程助手 / 排产系统**安全调用灵境制造的制造智能，而不需要打开桌面应用——这是"让已有系统调用制造智能"的生态接口。

```
外部 AI Agent（Claude Code / Cursor / 自研 Agent / 排产系统）
        │  MCP 协议（stdio 本地 / SSE 远程）
        ▼
mcp_server（本模块：FastMCP，工具注册与鉴权）
        │  HTTP + Bearer（LINGJING_AGENT_TOKEN）
        ▼
FastAPI 后端（engineering/python，localhost:8765）
```

安全模型（fail-closed）：

| 机制 | 说明 |
|---|---|
| `LINGJING_AGENT_TOKEN` | MCP 工具调后端用的 Bearer 令牌，**import 时即强校验 ≥32 字符**，缺失直接拒绝启动 |
| SSE 入站鉴权 | 远程暴露必须 `LNN_MCP_ALLOW_REMOTE=1` **且** `LINGJING_MCP_INGRESS_TOKEN`（≥32 字符），二者缺一即拒绝绑定 |
| 默认回环绑定 | SSE 默认只绑 `127.0.0.1`，不经反代不对外 |
| `hmac.compare_digest` | 令牌比较防时序侧信道 |

## 2. 启动

前置：后端已运行（`engineering/python/desktop_runtime/runtime/python.exe start_server.py`），`LINGJING_AGENT_TOKEN` 与后端配置一致（≥32 字符）。

```bash
# 仓库根目录执行
# 方式一：stdio（本地 MCP 客户端：Claude Code / Cursor）
LINGJING_AGENT_TOKEN=<≥32字符令牌> python -m mcp_server.server --transport stdio

# 方式二：SSE（远程 Agent，回环默认；远程暴露见 §1 安全模型）
LINGJING_AGENT_TOKEN=<≥32字符令牌> python -m mcp_server.server --transport sse --port 8080
```

## 3. 工具面清单（26 个）

| 组 | 工具 | 说明 | 独立开关（默认开） |
|---|---|---|---|
| **LNN 模型（6）** | `lnn_list_models` | 列出已注册 LNN 模型及元数据 | — |
| | `lnn_get_model_info` | 模型架构/参数量/性能详情 | |
| | `lnn_predict` | 预测推理（输入维度须匹配） | |
| | `lnn_train` | 发起异步训练，返回 job_id | |
| | `lnn_get_train_status` | 查询训练状态 | |
| | `lnn_wait_for_training` | 轮询至终态 | |
| **G 代码任务（5，写类 job 化）** | `gcode_create_job` | 发起 G 代码生成任务（输入目录白名单内） | `LINGJING_MCP_GCODE_TOOLS=0` 关闭 |
| | `gcode_get_job_status` | 任务详情（含 `include_gcode` 取全文） | |
| | `gcode_list_jobs` | 任务列表 | |
| | `gcode_list_failure_cases` | 失败案例（含 L1-L6 安全门禁分类） | |
| | `gcode_get_failure_stats` | 一次通过率/失败分布基线报表 | |
| **设备（7，按能力自动生成）** | `cnc_mill_01_read_status` / `move_axis` / `set_feed_rate` / `start_spindle` / `stop_spindle` | 仿真铣床状态与动作（参数越界 fail-closed） | `LINGJING_MCP_FACTORY_TOOLS=0` 关闭 |
| | `vib_sensor_01_read_status` / `reset` | 仿真加速度计（颤振监测）信号快照 | 同上 |
| **仿真工厂（4）** | `factory_get_status` / `factory_get_kpis` | 工厂状态 / KPI | 同上 |
| | `factory_run_cycle` / `factory_step` | 推进一个生产周期 / 仿真 tick | 同上 |
| **CAM 只读（2）** | `cam_recommend_process` | 工艺四元组推荐（失败案例库支撑） | `LINGJING_MCP_CAM_TOOLS=0` 关闭 |
| | `cam_get_quadruple_stats` | 四元组库统计 | 同上 |
| **DXF（1）** | `dxf_describe_file` | DXF 图纸结构化描述（只读） | `LINGJING_MCP_DXF_TOOLS=0` 关闭 |
| **工艺规划（1，写类同步）** | `process_plan_run` | 端到端：孔特征识别→参数匹配→工序排序→G 代码 | `LINGJING_MCP_PROCESS_TOOLS=0` 关闭 |

> 红线（与叙事纪律一致）：`gcode_create_job` / `process_plan_run` 的产出描述均写明"须经 CAM 软件二次校验，绝不直接接口 CNC 控制器"。

## 4. 五分钟接入示例

### 4.1 通用 MCP 客户端配置（Claude Code / Cursor / 其他）

```json
{
  "mcpServers": {
    "lingjing-mcp": {
      "command": "python",
      "args": ["-m", "mcp_server.server", "--transport", "stdio"],
      "cwd": "<仓库根目录绝对路径>",
      "env": {
        "LINGJING_AGENT_TOKEN": "<与后端一致的≥32字符令牌>",
        "LINGJING_API_URL": "http://127.0.0.1:8765"
      }
    }
  }
}
```

Claude Code 命令行等价写法：

```bash
claude mcp add lingjing-mcp --env LINGJING_AGENT_TOKEN=<≥32字符令牌> -- python -m mcp_server.server --transport stdio
```

### 4.2 最小验证脚本（不依赖后端即可验握手与工具面）

保存为 `check_mcp.py`，在仓库根目录运行：

```python
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server", "--transport", "stdio"],
        cwd=os.getcwd(),
        env={**os.environ, "LINGJING_AGENT_TOKEN": os.environ["LINGJING_AGENT_TOKEN"]},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"{len(names)} tools registered")
            for name in names:
                print(" -", name)


asyncio.run(main())
```

预期输出：`26 tools registered` 及 §3 的工具名清单。工具调用需要后端在线；`list_tools` 只验证网关自身。

## 5. 工具面变更纪律

1. 改注册代码（`mcp_server/*_tools.py` 的 `register_*`）；
2. 跑 §4.2 脚本确认新清单；
3. 更新本文 §3 表格与工具总数；
4. 同步《产品叙事与战略对标-2026-09》W10.1 的工具数口径（以本文为准）。

## 相关文档

- `PROJECT_OVERVIEW.md` §3.5（MCP 网关职责与令牌要求）
- `docs/产品叙事与战略对标-2026-09.md` §4.6 W10.1（生态接口叙事）
- `mcp_server/server.py`（传输/鉴权实现，fail-closed 语义以此为准）
