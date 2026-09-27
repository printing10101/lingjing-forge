# P3-2 补充：Router 封装工厂（createAppRouter）

**创建日期**: 2026-08-21  
**关联**: `docs/development/Store-Router-封装层.md`（v1.0 的 Router 部分已落地）

---

## 本轮新增

### `src/router/createAppRouter.ts`

统一创建 vue-router 实例：

| 能力 | 说明 |
|---|---|
| `createAppRouter(options)` | 工厂：routes + 认证守卫 + 标题 + 404 fallback |
| `defaultRequireAuth(to)` | 默认认证判定（public meta 免认证） |
| `defaultHasSession()` | 默认会话检测（localStorage token） |
| `findDuplicatePaths(routes)` | 重复 path 检测（防误注册） |

设计要点：
- 认证守卫返回 redirect（带 redirect 查询参数回跳）
- 标题后置守卫（route meta.title + 后缀）
- 404 fallback 自动注册
- 守卫/校验均为纯函数 → 可单测

### 测试
`engineering/src/router/__tests__/createAppRouter.test.ts`（~11 用例）：
默认认证判定 / 会话检测 / 404 / 未认证重定向 / 认证通过 / public 绕过 /
onUnauthorized 回调 / 标题 / 自定义守卫 / 重复路径检测。

## 验收标准（门禁）

1. vue-tsc 类型检查通过
2. vitest 用例全绿（守卫纯函数零挂载）
3. eslint 干净
4. 既有路由迁移后行为等价（回归）

## 变更日志

### v1.1 (2026-08-21)
- `createAppRouter.ts` 落地
- 测试 11 用例落地
- 待办：`src/router/index.ts` 接入工厂（文件锁解除后）
