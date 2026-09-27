# P2-3 数据采集 API 设计文档

> **版本**: 1.0.0  
> **创建日期**: 2026-08-26  
> **完成日期**: 2026-08-26 (发现现有实现)  
> **状态**: 已有完整实现，待测试门禁

---

## 1. 概述

**任务名称**: 数据采集 API  
**文件位置**: `app/api/v1/cutting_experience/routes.py`（P2-3，统一异常体系重构后）  
**功能定位**: 数据飞轮闭环的关键环节——采集 → 存储 → 分析 → 优化 → 再采集

> 2026-08-28 变更：原 `experience_routes.py`（前缀 `/api/cutting/experience`，无权限控制）
> 已由本模块替代并删除。统一前缀 `/api/v1/experience`（与前端 `@/api/cuttingExperience`
> 对齐），错误处理接入 `app.core.exceptions` 分级异常（503→2002 / 404→3002 / 422→1002）。

### 1.1 设计目标

1. **数据采集**: 支持单条/批量上传切削实测数据
2. **API 设计**: RESTful + 权限控制 (`experience:read`/`experience:write`)
3. **错误处理**: 数据库未配置 → 503，验证失败 → 400/422，记录不存在 → 404
4. **文档化**: OpenAPI Schema 自动生成（Swagger UI）

---

## 2. API 接口设计

### 2.1 端点列表

| 方法 | 路径 | 功能 | 权限 |
|------|------|------|------|
| POST | `/experience/capture` | 单条采集 | `experience:write` |
| POST | `/experience/batch` | 批量采集 (≤1000 条) | `experience:write` |
| GET | `/experience` | 分页查询 + 筛选 | `experience:read` |
| GET | `/experience/{id}` | 单条详情 | `experience:read` |
| GET | `/experience/stats` | 聚合统计 | `experience:read` |
| DELETE | `/experience/{id}` | 删除记录 | `experience:write` |

### 2.2 请求/响应模型

#### 单条采集 (`POST /capture`)

**请求体**:
```json
{
  "id": "exp_...",
  "job_id": "uuid",
  "machine_id": "VM-001",
  "tool_id": "T-12",
  "material": "AL6061",
  "parameters": {
    "depth_of_cut_mm": 1.5,
    "feed_mm_per_rev": 0.15,
    "spindle_rpm": 6000,
    "coolant": "flood"
  },
  "results": {
    "cycle_time_s": 120.5,
    "surface_roughness_ra": 1.2,
    "result": "ok"
  },
  "source": "manual"
}
```

**响应**:
```json
{
  "id": "exp_...",
  "machine_id": "VM-001",
  "created_at": "2026-08-26T10:30:00Z",
  ...
}
```

#### 批量采集 (`POST /batch`)

**限制**: 单次最多 1000 条（防止 OOM）

**请求体**:
```json
[
  { /* CuttingExperience 对象 1 */ },
  { /* CuttingExperience 对象 2 */ }
]
```

**响应**:
```json
{
  "inserted": 3,
  "requested": 3
}
```

#### 分页查询 (`GET /`)

**查询参数**:
- `machine_id` (可选): 机床 ID
- `tool_id` (可选): 刀具 ID
- `material` (可选): 工件材料
- `machining_type` (可选): 加工类型（milling/turning/drilling/tapping/boring/grooving/threading）
- `result` (可选): 加工结果（OK/REWORK/SCRAP）
- `has_anomaly` (可选): 是否有异常（true/false）
- `start_time` (可选): 开始时间（ISO 8601）
- `end_time` (可选): 结束时间（ISO 8601）
- `limit` (默认 100): 每页条数（1-1000）
- `offset` (默认 0): 偏移量

**响应**:
```json
{
  "records": [
    { /* CuttingExperience 对象 */ }
  ],
  "total": 158,
  "limit": 100,
  "offset": 0
}
```

#### 聚合统计 (`GET /stats`)

**查询参数**:
- `machine_id` (可选): 机床 ID
- `tool_id` (可选): 刀具 ID

**响应**:
```json
{
  "total_records": 158,
  "avg_cycle_time_s": 135.6,
  "avg_surface_roughness_ra": 1.35,
  "avg_tool_wear_percent": 45.2,
  "ok_rate": 0.92,
  "anomaly_rate": 0.08
}
```

#### 单条详情 (`GET /{id}`)

**路径参数**:
- `id`: 记录 ID（UUID 或 exp_{hex} 格式）

**响应**:
```json
{
  "id": "exp_...",
  "machine_id": "VM-001",
  "tool_id": "T-12",
  "parameters": { /* 完整参数对象 */ },
  "results": { /* 完整结果对象 */ },
  "created_at": "2026-08-26T10:30:00Z"
}
```

#### 删除记录 (`DELETE /{id}`)

**响应**:
```json
{
  "deleted": true,
  "id": "exp_..."
}
```

---

## 3. 实现细节

### 3.1 路由注册

文件：`app/api/v1/cutting_experience/routes.py`

**Router 定义**:
```python
router = APIRouter(prefix="/api/v1/experience", tags=["cutting-experience"])
```

**注册位置**: `app/api/routers/engineering.py`（工程域聚合器）
```python
from app.api.v1.cutting_experience.routes import router as cutting_experience_router
app.include_router(cutting_experience_router)
```

最终完整路径：`/api/v1/experience/*`

### 3.2 权限控制

使用 `app.auth.permissions.require_permission`：
- **写操作**（POST/DELETE）: `experience:write`
- **读操作**（GET）: `experience:read`

示例：
```python
@router.post("/capture", status_code=201)
async def capture_experience(
    payload: CuttingExperience,
    _: None = Depends(require_permission("experience:write")),
) -> dict:
    ...
```

### 3.3 错误处理

| HTTP 码 | 场景 | 原因 |
|---------|------|------|
| 201 | 创建成功 | POST /capture, POST /batch |
| 200 | 查询/统计/删除成功 | GET /, GET /{id}, DELETE /{id} |
| 400 | 请求体验证失败 | Pydantic 验证错误 |
| 404 | 记录不存在 | GET /{id} (ID 无记录), DELETE /{id} (ID 无记录) |
| 422 | 批量上传超限 | POST /batch > 1000 条 |
| 503 | 数据库未配置 | RuntimeError 捕获 |

示例：
```python
try:
    return await create_cutting_experience(payload)
except RuntimeError as exc:
    raise HTTPException(status_code=503, detail=str(exc)) from exc
```

### 3.4 时间解析

辅助函数 `_parse_dt`:
```python
def _parse_dt(value: str | None) -> object:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"非法时间格式：{value}") from exc
```

---

## 4. 与现有模块关系

### 4.1 Repository 层

文件：`app/services/domain/cutting_experience_repository.py` (230 行)

**导入**:
```python
from app.services.domain.cutting_experience_repository import (
    aggregate_experience_stats,
    create_cutting_experience,
    create_many_cutting_experiences,
    delete_cutting_experience,
    get_cutting_experience,
    list_cutting_experiences,
)
```

### 4.2 契约层

文件：`app/contracts/cutting_experience.py` (188 行)

**导入**:
```python
from app.contracts.cutting_experience import (
    CuttingExperience,
    ExperienceQuery,
    MachiningResult,
    MachiningType,
)
```

### 4.3 权限模块

文件：`app/auth/permissions.py`

**功能**: `require_permission(permission: str)`

---

## 5. API 使用场景

### 5.1 手动录入

前端表单提交到 `/experience/capture`:
```javascript
fetch('/api/v1/experience/capture', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    machine_id: 'VM-001',
    tool_id: 'T-12',
    material: 'AL6061',
    parameters: { ... },
    results: { ... },
    source: 'manual'
  })
});
```

### 5.2 MTConnect 自动采集

MTConnect Agent 定时推送批量数据到 `/experience/batch`:
```javascript
// 每 5 秒上传最近 10 条实测数据
fetch('/api/v1/experience/batch', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify([
    { /* record 1 */ },
    { /* record 2 */ }
  ])
});
```

### 5.3 历史数据迁移

从 `app/data/cutting_parameters.json` 导入 158 条历史数据：
```python
with open('app/data/cutting_parameters.json') as f:
    data = json.load(f)

records = [CuttingExperience(**transform_row(row)) for row in data]
response = requests.post('/api/v1/experience/batch', json=records)
print(f"导入 {response.json()['inserted']} 条记录")
```

### 5.4 数据飞轮分析

前端仪表盘调用 `/experience/stats` 获取关键指标：
```javascript
fetch('/api/v1/experience/stats?machine_id=VM-001')
  .then(r => r.json())
  .then(stats => {
    console.log('平均节拍:', stats.avg_cycle_time_s);
    console.log('合格率:', stats.ok_rate);
    console.log('异常率:', stats.anomaly_rate);
  });
```

---

## 6. 门禁验证计划

### 6.1 静态检查

```bash
$ ruff check app/api/v1/cutting_experience/routes.py
# 目标：0 违规
```

### 6.2 功能测试

需要编写测试用例：
- Capture (单条创建)
- Batch (批量创建)
- Query (分页查询)
- Detail (单条详情)
- Stats (聚合统计)
- Delete (删除)
- 错误处理 (404/503)
- 权限控制 (未授权 → 403)

**目标**: ≥10 个测试用例，覆盖率 ≥90%

### 6.3 OpenAPI 文档

验证 Swagger UI 中的端点：
```
Swagger UI → /api/v1/experience/* 全部可见？
```

---

## 7. 未来扩展

### 7.1 高级功能（可选）

- **数据校验**: 连接机床数据库验证机号/刀具号是否存在
- **并发控制**: 批量上传时的去重（避免 MTConnect 重复推送）
- **缓存层**: 高频查询（如 `/stats`）加 Redis 缓存
- **WebSockets**: 实时更新采集数量（前端进度条）

### 7.2 下一步任务

- **P3-1/2**: 前端数据飞轮仪表盘
- **P4-2**: 参数优化模型训练（调用 `/stats` 作为监督信号）

---

## 8. 总结

**API 状态**: 已有完整实现（159 行）  
**测试状态**: 待编写测试用例  
**文档状态**: 本文档 + OpenAPI 自动生成  
**提交建议**: 创建测试用例后提交 `feat(api): 数据采集 API (P2-3)`

*最后更新：2026-08-26（P2-3 数据采集 API 已有实现）*
