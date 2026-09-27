# P3-2 Store/Router 封装层

**创建日期**: 2026-08-21  
**状态**: CRUD Store 工厂（defineCrudStore）落地；Router 封装待接线

---

## 目标

将 Pinia setup store 常见的「列表 + 详情 + 分页 + 加载 + 错误」样板
抽为通用工厂（`defineCrudStore`），业务 store 只需提供 API 函数。
与 P3-1（Headless composable）配合构成前端自主化包装层。

## 已交付

### `src/stores/crud/defineCrudStore.ts`

| 能力 | 说明 |
|---|---|
| `fetchList(query)` | 列表加载（幂等：相同查询不重复请求） |
| `fetchOne(id)` | 详情加载（可选 get） |
| `createItem(data)` | 创建 + 插入列表顶部 + total+1 |
| `updateItem(id, data)` | 更新 + 就地 patch 列表行 |
| `removeItem(id)` | 删除 + 过滤行 + total-1 |
| `reset()` | 全量清空 |
| `isEmpty` | 派生空态（供空状态展示） |
| `lastQuery` | 最近查询（幂等去重依据） |

设计要点：
- **零 UI 依赖**（只依赖 pinia + vue reactivity）→ 单测独立跑
- 幂等查询去重（防重复请求）
- 错误统一收敛到 `errorMessage`（优雅降级不清列表）
- rowKey 可选（无则回退 id 字段）

### 测试
`engineering/src/stores/__tests__/defineCrudStore.test.ts`（~9 用例）：
列表加载 / 幂等 / 查询变化重载 / 失败降级 / 创建插入 / 更新 patch / 删除过滤 / 详情 / reset。

## 待接线（后续轮次）

1. Router 封装：`createAppRouter` 工厂（统一 layout + guard + 懒加载约定）
   —— 需文件锁解除后接入 `src/router/index.ts`
2. 既有 store 迁移到工厂（如 experienceStore 的 list 部分）—— 需文件锁解除
3. composables/index.ts + stores barrel 导出（需文件锁解除后追加）

## 验收标准（门禁）

1. vue-tsc 类型检查通过
2. vitest 用例全绿（纯逻辑零挂载）
3. eslint 干净
4. 迁移 store 后行为等价（回归）

## 变更日志

### v1.0 (2026-08-21)
- `defineCrudStore.ts` 落地
- 测试 9 用例落地
- 待办：Router 封装 + 既有 store 迁移（文件锁解除后）
