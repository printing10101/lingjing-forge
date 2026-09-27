# P3-1 Element Plus Headless 包装层

**创建日期**: 2026-08-21  
**状态**: 首个 Headless composable（useDataTable）落地；更多组件待接线

---

## 目标

将 Element Plus 常用组件的「交互逻辑」抽为纯逻辑 composable（Headless），
组件层只做模板绑定。收益：
1. **前端自主化占比提升**（路线图 Phase 3，目标 +15%）
2. **可独立单测**：纯逻辑不依赖组件挂载/Element Plus 环境
3. **UI 解耦**：未来可换 UI 库或写自定义组件复用同一逻辑

## 已交付

### `src/composables/headless/useDataTable.ts`（首个 Headless 包装）

通用数据表格逻辑（`el-table` + `el-pagination` 常用能力）：

| 能力 | 说明 |
|---|---|
| `clampPage(page, total, size)` | 页码钳制（越界回退，空集→1） |
| `nextDirection(dir)` | 排序方向轮转 asc→desc→null |
| `useDataTable({fetcher, pageSize, defaultSort, rowKey})` | 分页/排序/多选/加载状态 |
| `setPage / setPageSize` | 幂等（相同页码不重载）；size 变化重置到第 1 页 |
| `sortBy(prop)` | 排序轮转 + 重置页码 |
| `toggleRow / toggleSelectAll / clearSelection` | 多选去重、当前页全选/取消 |
| `selectedRows / isAllSelected` | 派生选择状态（行键过滤） |
| `reload` | 失败时 errorMessage + 清空 items（优雅降级） |
| `setExtraQuery(q)` | 透传额外查询参数（如材料筛选） |

设计要点：
- **零 Element Plus import**（只依赖 vue reactivity）→ 单测独立跑
- 页码越界自动钳制（删除末页最后一行后回退）
- reload 幂等保护 + 失败降级

### 测试
`engineering/src/composables/__tests__/useDataTable.test.ts`（~14 用例）：
钳制边界 / 方向轮转 / 分页幂等 / 排序轮转 / 多选去重 / 全选 / 失败降级 /
页码回退 / 额外查询透传。

## 待接线（后续轮次）

1. 更多 Headless composable（建议按需追加）：
   - `useHeadlessForm`（校验/提交状态，el-form）
   - `useHeadlessDialog`（开关/确认，el-dialog + el-message-box）
   - `useHeadlessTree`（懒加载/勾选，el-tree）
2. 迁移既有组件使用（如 ExperienceCapture / 各表格视图）——需文件锁解除后
3. composables/index.ts 导出（需文件锁解除后追加）

## 验收标准（门禁）

1. vue-tsc 类型检查通过
2. vitest 用例全绿（纯逻辑零挂载）
3. eslint 干净
4. 迁移组件后行为等价（回归）

## 变更日志

### v1.0 (2026-08-21)
- `useDataTable.ts` Headless composable 落地
- 测试 14 用例落地
- 待办：更多 Headless + 既有组件迁移（文件锁解除后）
