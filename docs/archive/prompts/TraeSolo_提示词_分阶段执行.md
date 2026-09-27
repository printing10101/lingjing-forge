# 灵境制造 V2.0 Release — Trae Solo 模式提示词（分阶段执行）

> **使用说明**：将每个提示词完整复制到 Trae Solo 模式，按 Phase 顺序执行。每步完成后运行对应的检测命令验证效果。**本文档只提供提示词，不输出具体代码。**

---

## Phase 1：安全合规冲刺（Week 1-3）

### Prompt 1.1 — 修复路径遍历漏洞（3处文件下载端点）

```
【任务背景】
系统存在3处路径遍历漏洞，攻击者可通过"../"遍历到服务器任意目录下载文件。

【需要修改的文件】
1. python/app/dxf/api.py — download_model 函数
2. python/app/step_import/api.py — get_output_file 函数
3. python/app/simulation/api.py — get_simulation_output 函数

【修复要求】
1. 对用户传入的 file_name 参数进行路径净化：提取纯文件名，拒绝包含路径分隔符（/ 或 \\）或 ".." 的输入
2. 使用 pathlib 的 resolve() 和 is_relative_to() 验证最终路径是否在允许的输出目录内
3. 如果验证失败，返回 HTTP 400 Bad Request 错误
4. 保持原有功能不变：文件存在性检查、媒体类型判断、FileResponse 返回

【验收检测】
1. 运行以下命令检查代码中是否使用了路径验证：
   grep -n "is_relative_to" python/app/dxf/api.py python/app/step_import/api.py python/app/simulation/api.py
   预期：每个文件至少出现1次

2. 运行以下命令确认无 eval() 或 exec() 被引入：
   grep -n "eval\|exec" python/app/dxf/api.py python/app/step_import/api.py python/app/simulation/api.py
   预期：无匹配（注释除外）

3. 启动服务后测试：
   curl -I "http://localhost:8765/api/model/download/../etc/passwd"
   预期返回 HTTP 400

4. 正常文件请求测试：
   curl -I "http://localhost:8765/api/model/download/test.stl"
   预期返回 HTTP 404（文件不存在）或 200（文件存在）
```

---

### Prompt 1.2 — 移除 eval() 调用

```
【任务背景】
python/app/rules/safety_constraint_rules.py 中使用 eval() 执行数学表达式，存在代码注入风险。

【需要修改的文件】
python/app/rules/safety_constraint_rules.py — 包含 eval() 的函数

【修复要求】
1. 将 eval() 替换为安全的数学表达式求值方案，可选方案：
   - 使用 ast.literal_eval()（仅支持基本运算需额外处理）
   - 或编写一个简单的递归下降解析器，只支持 +、-、*、/ 和浮点数
   - 或使用第三方安全库如 simpleeval
2. 如果表达式无法解析或包含非法字符，返回 0.0（保持原有降级行为）
3. 保持原有函数的输入输出签名不变

【验收检测】
1. 运行以下命令确认 eval() 已被移除：
   grep -n "eval(" python/app/rules/safety_constraint_rules.py
   预期：无匹配（注释除外）

2. 运行以下命令确认 ast 或安全解析器被引入：
   grep -n "import ast\|simpleeval\|def.*eval" python/app/rules/safety_constraint_rules.py
   预期：有匹配

3. 单元测试验证：
   - 正常表达式 "10.5+20.3*2" 应返回 51.1
   - 恶意表达式 "__import__('os').system('echo hack')" 应返回 0.0 或抛出异常
```

---

### Prompt 1.3 — 加固 exec() 沙箱

```
【任务背景】
python/app/core/skill_loader.py 使用 exec() 执行动态加载的技能代码，沙箱中的 _SAFE_BUILTINS 包含 __import__，可被绕过。

【需要修改的文件】
python/app/core/skill_loader.py — _SAFE_BUILTINS 定义和 _compile_code 方法

【修复要求】
1. 从 _SAFE_BUILTINS 中移除以下危险内置函数：
   - __import__
   - type
   - vars
   - dir
   - getattr
   - setattr
   - delattr
   - open
   - compile
   - eval
   - exec
2. 只允许真正安全的纯计算函数：abs、len、range、str、int、float、sum、min、max、round、pow、divmod 等
3. 添加代码注释说明沙箱限制范围
4. 考虑在独立进程中执行不受信任的代码（可选增强）

【验收检测】
1. 运行以下命令确认 __import__ 不在 _SAFE_BUILTINS 中：
   grep -A 20 "_SAFE_BUILTINS" python/app/core/skill_loader.py | grep "__import__"
   预期：无匹配

2. 运行以下命令确认 type 不在 _SAFE_BUILTINS 中：
   grep -A 20 "_SAFE_BUILTINS" python/app/core/skill_loader.py | grep "\"type\""
   预期：无匹配

3. 单元测试验证：尝试执行包含 __import__('os') 的技能代码，应抛出 SecurityError 或 ImportError
```

---

### Prompt 1.4 — 关闭开放注册 + 启用权限强制检查

```
【任务背景】
1. /api/v1/auth/register 端点对所有人开放，任何人可注册账号
2. LNN_PERMISSION_ENFORCED 默认值为 False，权限检查默认关闭

【需要修改的文件】
1. python/app/api/v1/auth.py — register 函数
2. python/app/config.py — permission_enforced 默认值

【修复要求】
1. 注册端点改造：
   - 添加邀请码验证机制（从环境变量 LNN_REGISTRATION_CODE 读取）
   - 如果环境变量未设置，返回 HTTP 403 禁止注册
   - 如果邀请码不匹配，返回 HTTP 403
   - 保留现有用户名存在性检查
   - 添加注册速率限制：同一IP每小时最多5次注册尝试

2. 权限配置改造：
   - 将 LNN_PERMISSION_ENFORCED 默认值从 False 改为 True
   - 添加启动时警告：如果权限检查被显式关闭，输出 WARNING 级别日志
   - 确保权限中间件在权限关闭时仍然记录访问日志

【验收检测】
1. 运行以下命令确认默认值已改：
   grep -n "LNN_PERMISSION_ENFORCED" python/app/config.py
   预期：默认值为 True

2. 运行以下命令确认注册端点有邀请码检查：
   grep -n "invite_code\|registration_code\|403" python/app/api/v1/auth.py
   预期：有匹配

3. API测试：
   - POST /api/v1/auth/register {"username":"test","password":"pass"} — 预期 403
   - POST /api/v1/auth/register {"username":"test","password":"pass","invite_code":"正确码"} — 预期 200 或 409（用户名已存在）
```

---

### Prompt 1.5 — 修复 Token 默认最高权限

```
【任务背景】
当 token 元数据文件不存在时，系统默认返回 {"level": "T"}（最高执行权限）。

【需要修改的文件】
python/app/core/middleware/unified_auth.py — _get_token_metadata 函数

【修复要求】
1. 将默认权限从 "T" 改为 "R"（最低只读权限）
2. 添加日志记录：当使用默认权限时，输出 WARNING 日志提示管理员配置 token 元数据文件
3. 保持现有 token 元数据文件解析逻辑不变
4. 确保现有已配置 token 的用户不受影响

【验收检测】
1. 运行以下命令确认默认返回 R：
   grep -A 3 "meta_file.exists()" python/app/core/middleware/unified_auth.py
   预期：看到 return {"level": "R"} 或类似代码

2. 单元测试验证：临时移除元数据文件后获取 token 元数据，level 应为 "R"
```

---

### Prompt 1.6 — 升级依赖修复 CVE 漏洞

```
【任务背景】
pip-audit 发现 39 个已知 CVE 漏洞，包括 cryptography、python-jose、python-multipart、langchain 等高危漏洞。

【需要修改的文件】
requirements.txt

【修复要求】
1. 升级以下依赖到安全版本：
   - cryptography >= 48.0.0
   - python-jose >= 3.5.0
   - python-multipart >= 0.0.28
   - langchain >= 1.3.0
   - langchain-community >= 0.4.1
   - protobuf >= 5.29.6
   - requests >= 2.34.2
   - urllib3 >= 2.7.0
2. 检查 transformers 是否需要升级（CVE-2026-1839）
3. 升级后运行现有测试套件确认兼容性

【验收检测】
1. 运行：pip-audit -r requirements.txt
   预期：无 HIGH 或 CRITICAL 级别漏洞

2. 运行：pytest tests/ -x --tb=short
   预期：所有测试通过

3. 运行：python -c "import app.main; print('OK')"
   预期：输出 OK，无导入错误
```

---

### Prompt 1.7 — 修复 docker-compose 安全配置

```
【任务背景】
docker-compose.yml 中存在硬编码密码和过多端口暴露。

【需要修改的文件】
docker-compose.yml、.env.example、.gitignore

【修复要求】
1. 创建 .env.example 模板文件，包含所有需要的环境变量，密码字段留空或标注说明
2. 将 docker-compose.yml 中的硬编码密码替换为 ${VAR_NAME} 语法引用环境变量
3. 移除 Redis（6379）和 PostgreSQL（5432）的端口映射，只在容器网络内通信
4. 保留 API（8000）、Grafana（3000）、Prometheus（9090）的端口映射
5. 更新 .gitignore 确保 .env 文件不被提交
6. 在 docker-compose.yml 顶部添加注释说明如何使用 .env 文件

【验收检测】
1. 运行：docker compose config
   预期：成功解析，无硬编码密码

2. 运行：grep -n "lnn_password\|admin" docker-compose.yml
   预期：无匹配（注释除外）

3. 检查 .env.example 存在且包含：DB_URL、POSTGRES_PASSWORD、GF_SECURITY_ADMIN_PASSWORD 等变量

4. 检查 .gitignore 包含 .env
```

---

### Prompt 1.8 — 启用全局速率限制

```
【任务背景】
RATE_LIMIT_ENABLED 默认值为 False，常规 API 端点无速率限制保护。

【需要修改的文件】
python/app/config.py、python/app/main.py

【修复要求】
1. 将 RATE_LIMIT_ENABLED 默认值改为 True
2. 在 main.py 中为关键端点添加速率限制：
   - /api/v1/auth/login: 5次/分钟
   - /api/v1/auth/register: 3次/小时
   - /api/v1/lnn/predict: 60次/分钟
   - /api/v1/lnn/train: 5次/小时
3. 使用 slowapi 或 FastAPI-limiter 库实现
4. 添加速率限制超出时的友好错误提示（中文）
5. 保留 Agent API 独立的速率限制逻辑

【验收检测】
1. 运行以下命令确认默认值已改：
   grep -n "RATE_LIMIT_ENABLED" python/app/config.py
   预期：默认值为 True

2. 运行以下命令确认限流中间件已注册：
   grep -n "rate_limit\|RateLimit\|Limiter" python/app/main.py
   预期：有匹配

3. API测试：快速发送6次登录请求，第6次应返回 HTTP 429
```

---

### Prompt 1.9 — 修复 CORS 配置风险

```
【任务背景】
开发模式使用通配符 * 同时 allow_credentials=True，存在跨域攻击风险。

【需要修改的文件】
python/app/core/cors_config.py

【修复要求】
1. 移除开发模式中的通配符 *，改为明确的 localhost 端口列表（如 http://localhost:5173, http://localhost:3000）
2. 添加启动时 CORS 配置验证：如果 origins 包含 * 且 allow_credentials=True，输出 ERROR 日志并拒绝启动
3. 生产模式只允许配置的域名
4. 添加 CORS 配置文档注释说明安全风险

【验收检测】
1. 运行以下命令确认无通配符：
   grep -n "\"\\*\"" python/app/core/cors_config.py
   预期：无匹配

2. 运行以下命令确认有明确的 localhost 配置：
   grep -n "localhost" python/app/core/cors_config.py
   预期：有匹配

3. 单元测试验证：配置 origins=["*"] + allow_credentials=True 时启动应失败
```

---

## Phase 2：功能闭环冲刺（Week 3-8）

### Prompt 2.1 — 修复 Workspace.vue HTTP 客户端

```
【任务背景】
Workspace.vue 直接使用 axios 而非封装的 http.ts，导致 Token 不自动附带、错误提示不生效。

【需要修改的文件】
src/views/Workspace.vue

【修复要求】
1. 将 import axios from 'axios' 替换为 import http from '@/utils/http'
2. 将所有 axios.post/axios.get 调用替换为 http.post/http.get
3. 检查文件中是否还有其他直接使用 axios 的地方，一并替换
4. 保持原有请求参数和响应处理逻辑不变
5. 确保错误处理仍然有效（http 拦截器会自动处理401/403等错误）

【验收检测】
1. 运行以下命令确认不再直接导入 axios：
   grep -n "import axios" src/views/Workspace.vue
   预期：无匹配

2. 运行以下命令确认导入了封装的 http：
   grep -n "import.*http.*from.*@/utils/http" src/views/Workspace.vue
   预期：有匹配

3. 构建验证：npm run build
   预期：构建成功，无错误

4. 浏览器验证：打开 Workspace 页面，执行推理请求，检查请求头中包含 Authorization: Bearer <token>
```

---

### Prompt 2.2 — 首页接入真实系统状态

```
【任务背景】
Home.vue 中 AI 服务状态和模型注册数均为硬编码，不反映真实状态。

【需要修改的文件】
src/views/Home.vue

【修复要求】
1. 在 script setup 中添加 onMounted 生命周期钩子
2. 调用 /api/health 端点获取系统健康状态
3. 调用 /api/v1/lnn/models 端点获取模型注册数量
4. 添加 loading 状态，数据加载前显示加载动画
5. 如果 API 调用失败，显示"状态获取失败"而非硬编码数据
6. 保持原有 UI 布局和样式

【验收检测】
1. 运行以下命令确认使用了 onMounted：
   grep -n "onMounted" src/views/Home.vue
   预期：有匹配

2. 运行以下命令确认调用了健康检查 API：
   grep -n "/api/health" src/views/Home.vue
   预期：有匹配

3. 浏览器验证：打开首页，检查 Network 面板中有 /api/health 和 /api/v1/lnn/models 请求

4. 模拟 API 失败（停止后端），确认显示"状态获取失败"
```

---

### Prompt 2.3 — 开发 DXF 导入前端界面

```
【任务背景】
后端已有完整的 DXF 解析模块，但前端没有对应的 UI 组件，用户无法使用此功能。

【需要创建的文件】
src/components/dxf_import/DxfImportDialog.vue（或类似路径）

【修复要求】
1. 创建 DXF 导入对话框组件，包含：
   - 文件选择区域（拖拽或点击选择）
   - 上传进度显示
   - 解析结果展示（线段数、圆弧数、圆数、识别到的特征数）
   - 2D/3D 预览区域（使用现有 ThreeViewer 组件）
   - 导入到项目按钮
2. 调用后端 /api/dxf/upload 和 /api/dxf/parse 端点
3. 使用封装的 http 客户端（非直接 axios）
4. 添加错误处理（文件格式错误、解析失败等）
5. 在 App.vue 的"文件"菜单中添加"导入 DXF"入口

【验收检测】
1. 运行以下命令确认组件存在：
   ls src/components/dxf_import/
   预期：看到 DxfImportDialog.vue

2. 运行以下命令确认菜单入口已添加：
   grep -n "导入 DXF\|DXF" src/App.vue
   预期：有匹配

3. 浏览器验证：点击"文件→导入 DXF"，选择 DXF 文件，确认上传、解析、预览流程完整
```

---

### Prompt 2.4 — 开发工艺规划前端界面

```
【任务背景】
后端已有完整的工艺规划 Pipeline，但前端没有对应的 UI 组件。

【需要创建的文件】
src/views/ProcessPlanning.vue（或类似路径）

【修复要求】
1. 创建工艺规划页面，包含：
   - 左侧：特征列表（孔/凸台/型腔/平面），带复选框
   - 中间：工序树（自动生成的加工步骤），可展开/折叠
   - 右侧：G 代码预览区域
   - 底部：操作按钮（重新规划、导出 G 代码、仿真验证）
2. 调用后端 /api/process_planning/plan 端点获取工艺方案
3. 使用现有组件：ThreeViewer（3D预览）、ErrorConflictDialog（错误提示）
4. 在路由中添加 /process-planning 路径
5. 在 App.vue 导航栏中添加"工艺规划"入口

【验收检测】
1. 运行以下命令确认页面存在：
   ls src/views/ProcessPlanning.vue
   预期：文件存在

2. 运行以下命令确认路由已添加：
   grep -n "process-planning" src/router/index.ts
   预期：有匹配

3. 浏览器验证：访问 /process-planning，选择工件，确认特征列表→工序树→G代码预览流程完整
```

---

### Prompt 2.5 — 添加 404 兜底路由

```
【任务背景】
路由配置中没有 404 兜底路由，用户访问不存在的 URL 时看到空白页面。

【需要修改的文件】
src/router/index.ts
【需要创建的文件】
src/views/NotFound.vue

【修复要求】
1. 在 router/index.ts 中添加兜底路由：
   { path: '/:pathMatch(.*)*', name: 'NotFound', component: () => import('@/views/NotFound.vue') }
2. 创建 NotFound.vue 组件，包含：
   - 404 图标或插图
   - "页面不存在"提示文字
   - 返回首页按钮
   - 保持与系统 UI 风格一致（使用 Element Plus 组件）
3. 确保 404 页面不需要认证即可访问

【验收检测】
1. 运行以下命令确认兜底路由存在：
   grep -n "pathMatch" src/router/index.ts
   预期：有匹配

2. 运行以下命令确认 404 页面存在：
   ls src/views/NotFound.vue
   预期：文件存在

3. 浏览器验证：访问不存在的 URL（如 /nonexistent-page），确认显示 404 页面
```

---

### Prompt 2.6 — 权限不足时添加提示

```
【任务背景】
用户权限不足时直接跳转首页，没有任何提示信息。

【需要修改的文件】
src/router/index.ts

【修复要求】
1. 在权限检查失败、执行 next('/') 跳转前，添加提示：
   ElMessage.warning('权限不足，无法访问该页面')
2. 确保 ElMessage 已正确导入
3. 保持跳转逻辑不变

【验收检测】
1. 运行以下命令确认有权限提示：
   grep -n "权限不足\|ElMessage.warning" src/router/index.ts
   预期：有匹配

2. 浏览器验证：登录普通用户，尝试访问 /admin/users，确认显示"权限不足"提示后跳转首页
```

---

### Prompt 2.7 — 统一健康检查端点

```
【任务背景】
存在3个健康检查端点返回格式不一致，且 Dockerfile 使用的路径不存在。

【需要修改的文件】
python/app/main.py、python/app/api/v1/health.py、Dockerfile

【修复要求】
1. 统一健康检查端点：
   - 保留 /api/health 作为主健康检查端点，返回 {"status": "ok", "version": "x.x.x", "timestamp": "..."}
   - 保留 /api/health/ping 作为轻量级检查，返回 {"ping": true}
   - 移除 /health 端点（避免重复）
2. 更新 Dockerfile HEALTHCHECK 使用 /api/health/ping
3. 确保所有健康检查端点都在公开路径中（无需认证）

【验收检测】
1. API测试：
   curl http://localhost:8765/api/health
   预期：返回包含 status、version、timestamp 的 JSON

   curl http://localhost:8765/api/health/ping
   预期：返回 {"ping": true}

2. 运行以下命令确认 Dockerfile 路径正确：
   grep -n "HEALTHCHECK" Dockerfile
   预期：包含 /api/health/ping

3. 运行以下命令确认 /health 已移除：
   grep -n "@app.get(\"/health\"\|@app.get('/health')" python/app/main.py
   预期：无匹配
```

---

## Phase 3：性能与架构冲刺（Week 6-14）

### Prompt 3.1 — core/模块拆分

```
【任务背景】
python/app/core/ 目录包含 50+ 文件，混合了异常、日志、安全、任务、预算、插件等多种职责。

【需要修改的文件】
涉及多个文件的移动和重构

【修复要求】
1. 将 core/ 拆分为以下子包：
   - python/app/core/ — 仅保留：exceptions.py、exception_handlers.py、error_taxonomy.py、logging_config.py、log_sanitizer.py、response.py、request_id.py（5-8个文件）
   - python/app/auth/ — 从 core/middleware/ 迁移：security.py、permissions.py、middleware/unified_auth.py、middleware/security_headers_asgi.py
   - python/app/tasks/ — 从 core/ 迁移：task_system.py、task_manager.py、task_checkout.py、worker_process.py、execution.py、execution_lock.py
   - python/app/plugins/ — 从 core/ 迁移：plugin_system.py、plugin_worker.py、skill_loader.py、skill_marketplace.py
   - python/app/budget/ — 从 core/ 迁移：budget.py、budget_enforcer.py、cost_tracker.py、approval_workflow.py
   - python/app/goals/ — 从 core/ 迁移：goal_alignment.py、goal_chain_store.py
2. 更新所有导入语句
3. 确保无循环依赖

【验收检测】
1. 运行以下命令确认 core/ 文件数 < 10：
   ls python/app/core/*.py | wc -l
   预期：小于10

2. 运行以下命令确认应用可正常启动：
   python -c "from app.main import app; print('OK')"
   预期：输出 OK

3. 运行以下命令检查循环依赖：
   python -c "import app.main"
   预期：无 ImportError

4. 运行测试：pytest tests/ -x
   预期：通过
```

---

### Prompt 3.2 — 全局单例改为依赖注入

```
【任务背景】
34个文件58处使用 global _ 模式的全局变量单例，存在线程安全隐患和测试困难。

【需要修改的文件】
涉及多个文件，重点：
- python/app/database/connection.py
- python/app/services/redis_client.py
- python/app/rag/vector_store.py

【修复要求】
1. 使用 FastAPI Depends() 依赖注入系统管理以下单例：
   - 数据库引擎和会话
   - Redis 客户端
   - 向量存储
   - 其他全局状态
2. 创建对应的依赖函数（如 get_db()、get_redis()、get_vector_store()）
3. 在路由函数中使用 Depends(get_db) 等方式注入
4. 保持原有功能不变（连接池配置、降级逻辑等）

【验收检测】
1. 运行以下命令统计 global _ 模式数量：
   grep -rn "^_.*None$" python/app/ | grep -v __pycache__ | wc -l
   预期：显著减少（目标 < 10）

2. 运行以下命令确认 Depends 被广泛使用：
   grep -rn "Depends(" python/app/ | wc -l
   预期：显著增加

3. 运行测试：pytest tests/ -x
   预期：通过
```

---

### Prompt 3.3 — 异常处理规范化

```
【任务背景】
98个文件共439处使用了 except Exception，20个文件36处 except 块后直接 pass。

【修复要求】
1. 优先处理 36 处 except: pass：
   - 至少添加 logger.debug() 或 logger.warning() 记录异常信息
   - 如果静默吞掉是预期行为，添加注释说明原因
2. 逐步替换 439 处 except Exception：
   - 识别可替换为具体异常类型的场景（如 ValueError、KeyError、HTTPException 等）
   - 优先处理核心模块（LNN、仿真、工艺规划）
   - 保留合理的宽泛捕获（如降级处理、未知异常兜底）
3. 不追求一次全部改完，但核心模块必须完成

【验收检测】
1. 运行以下命令统计 except: pass 数量：
   grep -rn "except.*:.*pass$\|except.*:\s*pass" python/app/ | wc -l
   预期：0

2. 运行以下命令统计 except Exception 数量：
   grep -rn "except Exception" python/app/ | wc -l
   预期：显著减少（核心模块目标 < 20）

3. 运行测试：pytest tests/ -x
   预期：通过
```

---

### Prompt 3.4 — 清理临时脚本

```
【任务背景】
根目录和 python/ 目录下有 40+ 临时/调试脚本，污染代码库。

【需要处理的文件】
根目录和 python/ 目录下的 test_*.py、debug_*.py、diagnose*.py、quick_*.py 等（tests/ 目录下的除外）

【修复要求】
1. 删除以下类型的一次性脚本：
   - 诊断脚本：diagnose.py、diagnose2.py、diagnose3.py、deep_diag.py、quick_diag.py
   - 调试脚本：debug_train.py、debug_*.py
   - 临时测试：test_1_2_3.py、test_quick.py、test_ping.py 等（非 tests/ 目录）
   - 启动脚本整合：保留一个统一的 start_server.py，删除其他变体
   - .bak 文件：package.json.bak.* 等
2. 有用的测试脚本迁移到 tests/ 目录并规范化命名
3. 更新 .gitignore 防止未来临时文件被提交

【验收检测】
1. 运行以下命令检查 python/ 根目录临时脚本：
   ls python/*.py | grep -E "(test_|debug_|diagnose|quick_)" | wc -l
   预期：0

2. 运行以下命令检查根目录 .bak 文件：
   ls *.bak* 2>/dev/null | wc -l
   预期：0

3. 运行以下命令确认 .gitignore 已更新：
   grep -E "\*.bak|test_.*\.py|debug_.*\.py" .gitignore
   预期：有匹配
```

---

### Prompt 3.5 — Rust 体素化计算模块（关键路径）

```
【任务背景】
Python 体素仿真存在 O(N*V*T) 性能瓶颈，Rust 计算模块目前仅为占位。

【需要创建的文件】
src/compute/ 目录下的 Rust 源码文件

【修复要求】
1. 实现体素化核心算法（Rust + PyO3）：
   - 体素网格数据结构（替代 Python 三重循环）
   - 刀具体素化（支持6种刀具类型）
   - 切削仿真（逐点碰撞检测的向量化实现）
   - 结果导出（numpy 数组零拷贝返回）
2. 在 Python 侧创建适配层：
   - python/app/simulation/rust_engine.py（PyO3 模块封装）
   - 保持原有 VoxelCutter API 不变
   - 自动检测 Rust 模块可用性，不可用时回退到 Python 实现
3. 添加 Rust 单元测试

【验收检测】
1. 运行 Rust 测试：cargo test -p compute
   预期：全部通过

2. 运行 Python 测试：pytest tests/simulation/ -v
   预期：全部通过（含 Rust 和 Python 回退路径）

3. 性能基准测试：
   - 测试 100x100x100 体素网格的处理时间
   - 预期：相比纯 Python 实现提升 50%+

4. 运行以下命令确认 PyO3 模块可导入：
   python -c "from compute import voxel_cutter; print('Rust module loaded')"
   预期：输出 Rust module loaded
```

---

## Phase 4：工程化与体验冲刺（Week 12-18）

### Prompt 4.1 — 测试覆盖补强

```
【任务背景】
总体覆盖率 48.59%，核心模块 trainer.py 13.84%、dataset.py 13.47%，E2E 仅 2 个 spec。

【需要修改的文件】
tests/ 目录下新增/补充测试文件

【修复要求】
1. 为核心模块补充单元测试（目标覆盖率 ≥80%）：
   - python/app/ai/lnn/training/trainer.py
   - python/app/ai/lnn/dataset.py
   - python/app/simulation/voxel_cutter.py
   - python/app/process_planning/pipeline.py
2. 补充 E2E 测试（目标 10 个场景）：
   - 登录→工作区→推理→结果查看
   - STEP 导入→3D 预览→保存项目
   - 工艺规则编辑→冲突检测→保存
   - 刀具路径编辑→G 代码导出
   - 仿真运行→结果查看
   - 插件安装→配置→卸载
   - 用户管理→权限分配
   - 设置修改→持久化验证
   - 多语言切换→界面刷新
   - 离线模式→核心功能可用
3. 添加安全相关测试：
   - 路径遍历防护
   - 权限检查
   - 速率限制
   - Token 过期处理

【验收检测】
1. 运行覆盖率报告：pytest --cov=app tests/ --cov-report=term-missing
   预期：总体覆盖率 ≥75%，核心模块 ≥80%

2. 运行 E2E 测试：npx playwright test
   预期：10 个场景全部通过

3. 运行安全测试：pytest tests/security/ -v
   预期：全部通过
```

---

### Prompt 4.2 — 前端国际化补齐

```
【任务背景】
国际化覆盖率不足 50%，核心页面大量硬编码中文。

【需要修改的文件】
src/locales/zh-CN.ts、src/locales/en.ts、多个 Vue 组件

【修复要求】
1. 在 zh-CN.ts 和 en.ts 中添加缺失的翻译键，覆盖以下组件：
   - App.vue — 导航菜单（工艺规则、刀路编辑、用户管理、文件菜单等）
   - Workspace.vue — 工作区标题、标签页、按钮文字
   - Home.vue — 欢迎语、系统状态标签
   - StepImportDialog.vue — 上传提示、状态文字
   - RuleEditor.vue — 编辑器标题和描述
   - 新添加的 DXF/工艺规划页面
2. 将所有硬编码中文替换为 $t('key') 调用
3. 英文翻译需准确、专业（制造领域术语）

【验收检测】
1. 运行以下命令统计硬编码中文（粗略检查）：
   grep -rn "[\u4e00-\u9fff]" src/views/ src/components/ | grep -v "$t(" | wc -l
   预期：显著减少（目标 < 20 处注释和特殊场景除外）

2. 浏览器验证：切换语言为英文，检查上述页面中不再显示中文

3. 切换回中文，确认中文显示正常
```

---

### Prompt 4.3 — 一键安装脚本

```
【任务背景】
当前需要安装 Python/Rust/Node.js/Ollama 四套环境 + Git LFS，普通车间工程师难以独立部署。

【需要创建的文件】
scripts/install.ps1（Windows PowerShell 安装脚本）

【修复要求】
1. 创建 PowerShell 一键安装脚本，功能包括：
   - 检查系统要求（Windows 10+、磁盘空间 10GB+）
   - 自动下载并安装嵌入式 Python 3.11（不影响系统 Python）
   - 自动下载并安装 Ollama
   - 安装 VC++ 运行库（如需要）
   - 下载灵境制造应用包到 %LOCALAPPDATA%\LingjingManufacturing
   - 初始化数据库
   - 下载 LNN 基础模型和 Embedding 模型
   - 创建桌面快捷方式
2. 添加清晰的进度提示和错误处理
3. 支持静默安装模式（/silent 参数）

【验收检测】
1. 在干净的 Windows 虚拟机中运行安装脚本
   预期：10 分钟内完成安装，桌面出现快捷方式

2. 点击快捷方式启动应用
   预期：应用正常启动，无额外依赖安装提示

3. 验证核心功能可用：
   - LNN 推理
   - STEP 导入
   - 体素仿真
```

---

### Prompt 4.4 — Tauri 打包内置环境

```
【任务背景】
Tauri 桌面端目前仅作为壳，Python 后端仍需独立安装。

【需要修改的文件】
src-tauri/ 目录下的 Tauri 配置和 Sidecar 管理

【修复要求】
1. 配置 Tauri Sidecar 打包 Python 后端：
   - 将 Python 后端打包为可执行文件（使用 PyInstaller 或类似工具）
   - 配置 Tauri 在启动时自动启动 Sidecar Python 进程
   - 配置进程生命周期管理（启动、监控、重启、关闭）
2. 前端适配：
   - 检测 Sidecar 启动状态，显示启动进度
   - Sidecar 启动失败时显示友好错误提示
3. 构建配置：
   - 配置 Tauri 构建包含 Sidecar
   - 生成单文件安装包（.msi 或 .exe）

【验收检测】
1. 运行 Tauri 构建：npm run tauri build
   预期：成功生成安装包

2. 在干净环境中安装并运行
   预期：无需单独安装 Python，应用自动启动后端

3. 验证后端进程管理：
   - 应用关闭时 Python 进程正常退出
   - Python 进程崩溃时前端显示错误并可重启
```

---

### Prompt 4.5 — 用户手册与 API 文档完善

```
【任务背景】
文档与代码可能存在不同步，用户手册不完善。

【需要修改的文件】
docs/ 目录下新增/更新文档

【修复要求】
1. 更新用户手册，覆盖以下内容：
   - 安装指南（含一键安装脚本使用）
   - 快速入门（第一个工件从导入到 NC 代码）
   - 功能详解（每个核心功能的操作步骤）
   - 故障排查（常见问题及解决方案）
   - 安全须知（权限管理、数据保护）
2. 更新 API 文档：
   - 确保 OpenAPI 文档与代码同步
   - 添加请求/响应示例
   - 添加错误码说明
3. 更新开发文档：
   - 架构概述
   - 开发环境搭建
   - 测试指南
   - 贡献指南

【验收检测】
1. 检查 docs/ 目录结构完整性：
   ls docs/
   预期：包含 user-guide/、api/、development/ 等目录

2. 验证 OpenAPI 文档：
   访问 http://localhost:8765/api/docs
   预期：所有端点有描述、参数说明、响应示例

3. 文档与代码一致性检查：
   - 对比 API 文档中的端点路径与实际路由注册
   - 对比用户手册中的截图与当前 UI
```

---

## 附录：综合验证脚本

将以下内容保存为 `verify_release.py`，全部 Phase 完成后运行：

```
【验证脚本使用说明】
1. 确保后端服务运行在 localhost:8765
2. 确保前端构建完成并可访问
3. 运行：python verify_release.py
4. 所有检查项通过后方可标记为 Release Ready

【检查项清单】
□ S1: 路径遍历测试 — 访问 ../etc/passwd 返回 400
□ S2: eval/exec 检查 — 代码扫描无匹配
□ S3: 权限默认开启 — config.py 默认 True
□ S4: 速率限制 — 快速请求返回 429
□ S5: 依赖 CVE — pip-audit 无 HIGH/CRITICAL
□ S6: docker-compose 安全 — 无硬编码密码
□ F1: DXF 前端 — 可上传、解析、预览
□ F2: 工艺规划前端 — 可规划、查看 G 代码
□ F3: 404 页面 — 访问不存在 URL 显示 404
□ F4: 首页动态数据 — 来自 API 非硬编码
□ P1: 体素仿真性能 — 100mm/0.5mm < 30秒
□ P2: core/拆分 — 文件数 < 10
□ P3: 全局单例 — 显著减少
□ P4: 临时脚本 — python/ 根目录无 test_/debug_ 文件
□ T1: 覆盖率 — 总体 ≥75%，核心 ≥80%
□ T2: E2E — 10 个场景通过
□ U1: 国际化 — 英文模式无中文残留
□ U2: 一键安装 — 非技术用户 10 分钟完成
```

---

## 执行顺序速查表

| 顺序 | Prompt | Phase | 预计时间 | 阻断性 |
|------|--------|-------|----------|--------|
| 1 | 1.1 路径遍历修复 | P1安全 | 2h | 是 |
| 2 | 1.2 移除 eval() | P1安全 | 1.5h | 是 |
| 3 | 1.3 加固 exec() 沙箱 | P1安全 | 2h | 是 |
| 4 | 1.4 关闭开放注册 | P1安全 | 2h | 是 |
| 5 | 1.5 Token默认权限 | P1安全 | 1h | 是 |
| 6 | 1.6 升级依赖CVE | P1安全 | 2h | 是 |
| 7 | 1.7 docker-compose安全 | P1安全 | 1.5h | 否 |
| 8 | 1.8 启用速率限制 | P1安全 | 2h | 否 |
| 9 | 1.9 修复CORS | P1安全 | 1h | 否 |
| 10 | 2.1 Workspace HTTP | P2功能 | 2h | 是 |
| 11 | 2.2 首页动态数据 | P2功能 | 1.5h | 否 |
| 12 | 2.3 DXF前端 | P2功能 | 8h | 是 |
| 13 | 2.4 工艺规划前端 | P2功能 | 10h | 是 |
| 14 | 2.5 404路由 | P2功能 | 1h | 否 |
| 15 | 2.6 权限提示 | P2功能 | 0.5h | 否 |
| 16 | 2.7 健康检查统一 | P2功能 | 1h | 否 |
| 17 | 3.1 core/拆分 | P3架构 | 6h | 否 |
| 18 | 3.2 全局单例注入 | P3架构 | 4h | 否 |
| 19 | 3.3 异常处理规范 | P3架构 | 4h | 否 |
| 20 | 3.4 清理临时脚本 | P3架构 | 1h | 否 |
| 21 | 3.5 Rust体素化 | P3性能 | 20h | 是 |
| 22 | 4.1 测试覆盖补强 | P4工程 | 12h | 否 |
| 23 | 4.2 国际化补齐 | P4体验 | 4h | 否 |
| 24 | 4.3 一键安装脚本 | P4体验 | 4h | 否 |
| 25 | 4.4 Tauri打包 | P4体验 | 6h | 否 |
| 26 | 4.5 文档完善 | P4体验 | 6h | 否 |

> **阻断性**：标记为"是"的项必须在 Release 前完成，否则不可发布。
```