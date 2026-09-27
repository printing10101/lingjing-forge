# Postprocessor 方言声明化设计（Postprocessor Dialect Declaration Design）

**文档状态**: 提议（待 review）
**对应 ADR**: 建议后续开 ADR-022（本设计评审通过后）
**最后更新**: 2026-08-19
**适用版本**: 灵境制造 v2.7.0 → v3.0（插件化演进）

---

## 0. 文档目的与读者

本文档定义 CNC **后处理器（postprocessor）从"核心硬编码方言类"演进为"声明式方言插件"** 的详细设计，是"一切皆插件 / 工程师自由度"战略的第一个真实垂直切片。

**读者**：
- 项目负责人（review 与拍板）
- 工艺/设备工程师（方言声明化的最终用户）
- 后端实现者（引擎改造、注册桥、契约测试）
- 前端实现者（方言管理页、实时预览器）

**文档边界**：本文只做 postprocessor 方言声明化的设计决策与落地蓝图，不写实现代码。实现按本文分阶段推进，每阶段产出独立 PR。

---

## 1. 背景与动机

### 1.1 战略决策（2026-08-19 已确认）

| 决策项 | 结论 |
|--------|------|
| 战略方向 | 向 DSH（DeepSeek Harness，Cordis 插件生态）方向演进，核心极薄 + 能力可插拔 |
| 目标用户 | **工艺员（不写代码）** 优先，工程师次之 |
| 第一刀 | **postprocessor 方言声明化** |
| 市场形态 | **本地插件目录**（目录扫描即得，先不做远程分发） |

### 1.2 为什么第一刀是 postprocessor

1. **天然的高价值缝**：9 种机床方言（Fanuc/Siemens/Heidenhain/GSK/HNC/KND/Mitsubishi/Fagor/XMachine）是"工程师/工艺员最想自己加的东西"——每家工厂都有自己的机床和方言习惯。
2. **现状已半配置驱动**：`config/postprocessor_config.yaml`（1086 行）已实现 base + controllers 分层深度合并，`ConfigLoader` / `ConfigValidator` / `ConfigLimiter` 齐全。缺口在于**方言的模板逻辑仍硬编码在 Python 类里**。
3. **风险可控**：后处理是离线任务（无性能敏感路径），输出可做黄金测试（同输入 → 同 NC 字符串），迁移行为可验证。

### 1.3 现状证据（审计结论）

| 事实 | 证据 |
|------|------|
| 事实 | 证据 |
|------|------|
| 9 个方言类集中硬编码 | `app/postprocessor/{fanuc,siemens,heidenhain,gsk,hnc,knd,mitsubishi,fagor,xmachine}.py`（`_loader.VALID_CONTROLLER_IDS` 9 项） |
| 方言差异 ≈ 模板差异 + 少量代码钩子 | fanuc.py 与 gsk.py 对比：差异集中在 header/tool_change/arc/cycles/footer 的**字符串模板**与**参数开关** |
| 注册表已有动态注册能力 | `PostProcessorRegistry.register(controller_id, processor_cls)`（registry.py:83） |
| 配置层已声明化 | `postprocessor_config.yaml`：base + controllers.<name> 深度合并（`_loader._deep_merge`） |
| 校验/限幅已就绪 | `_validator.ConfigValidator`（完整性校验）、`_limiter.ConfigLimiter`（主轴/进给限幅） |
| 插件系统存在但未接电 | `init_plugin_system()` 全仓库无调用点；`/api/v1/plugins` 吞异常返回空列表（见 .dsh-memory 审计） |

> 本设计不解决插件系统整体接线问题（那是独立工作项），只保证方言声明化不依赖插件系统即可独立落地，同时**预留**接入插件系统的挂载点。

---

## 2. 目标与非目标

### 2.1 目标

1. **工艺员零代码加方言**：在本地插件目录放一个声明文件 + 模板文件，即可注册新机床方言。
2. **行为零变化**：9 个内置方言声明化前后，同输入生成的 NC 字符串完全一致（黄金测试保证）。
3. **模板可编辑可预览**：工艺员在界面上改模板/参数，实时看到 NC 输出预览。
4. **保留高级逃生舱**：需要特殊逻辑的方言（如 Heidenhain 的循环体系）可挂"代码钩子"，留给会写代码的工程师。

### 2.2 非目标（本设计明确不做）

- 不把 9 个方言类"搬进 plugins/ 目录"了事（那只是移动硬编码，不是声明化）。
- 不做远程插件市场/在线分发（本地目录先行）。
- 不重构 `ConfigLoader/Validator/Limiter` 的现有行为（它们是核心资产，只扩展）。
- 不解决插件系统全局接线（init_plugin_system 等，独立工作项）。
- 不引入完整工作流/任务系统改造。

---

## 3. 核心设计：方言 = 声明 + 模板 + 可选钩子

### 3.1 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  前端：方言管理页 + 新建方言向导 + NC 实时预览器（P3）            │
├─────────────────────────────────────────────────────────────────┤
│  API 层：/api/v1/postprocessor/dialects（CRUD + preview，P3）    │
├─────────────────────────────────────────────────────────────────┤
│  方言声明编译层（P1 已实现：app/postprocessor/dialect/）       │
│  ├─ declaration.py  DialectDeclaration + YAML 加载校验           │
│  ├─ compiler.py     DialectCompiler：extends 解析 + Jinja2 模板  │
│  │                  → 动态子类（模板方法替换，签名与基类一致）    │
│  └─ registry.py     DialectRegistry：目录扫描 + register_to      │
│                     （load_dialects 一键发现/编译/注册）          │
│  注册桥：PostProcessorRegistry（register 钩子复用，registry.py:83）│
│  引擎（保留核心）：BasePostProcessor + ConfigLoader/Validator/    │
│                    Limiter + _config_mixin + _format_mixin      │
└─────────────────────────────────────────────────────────────────┘
```

**核心原则**：引擎留核心（这是"必须留核心"的安全边界：格式校验 + 限幅 = NC 安全底线）；方言降为数据（声明 + 模板）。

**已实现的原型**（`postprocessor-plugins/knd_1000_2000_3000/`）：KND 声明镜像覆盖 4 个模板方法（header/tool_change/cycle_drill/cycle_tapping），其余方法继承 Fanuc 基类；其输出与内置 `KNDPostProcessor` 逐字符一致（三重验证）。

### 3.2 方言插件目录结构（本地插件目录）

```
postprocessor-plugins/                    ← 本地方言插件根目录（可配置）
└── gsk980/
    ├── dialect.yaml                      ← 方言声明（清单）
    └── templates/
        ├── header.j2                     ← 程序头模板
        ├── tool_change.j2                ← 换刀模板
        ├── arc.j2                        ← 圆弧模板
        ├── cycles.yaml                   ← 固定循环模板/参数（G73/G81/G83/G84/G86/G89/G76...）
        ├── subprogram.j2                 ← 子程序模板
        └── footer.j2                     ← 程序结尾模板
```

内置 9 方言保持原位（`app/postprocessor/*.py`）作为**引擎的黄金参考实现**，同时以声明形式镜像到插件目录用于验证；后续逐步把内置方言迁移为纯声明。

### 3.3 方言声明 schema（dialect.yaml）

```yaml
# 方言声明（v1）
id: gsk_980_25i                  # 控制器标识，与 CONTROLLER_ID_TO_FULL 对齐
name: GSK 980/25i (Guangzhou CNC)
version: "1.0.0"
extends: fanuc_0i                # 继承基础方言（模板/参数继承）
target_controller: gsk_980_25i

# 模板覆盖：仅声明与父方言不同的块；不声明则继承
templates:
  header: templates/header.j2
  tool_change: templates/tool_change.j2
  footer: templates/footer.j2
  # arc/cycles/subprogram 不声明 → 继承 fanuc_0i

# 参数覆盖（与 postprocessor_config.yaml 的 controllers.<name> 语义一致）
params:
  safe_z_height: 50.0
  spindle:
    max_rpm: 8000
  feed:
    max_rate: 6000.0

# 高级模式：需要代码钩子的方言（可选，默认无）
hooks: null
# hooks:
#   entrypoint: plugins.gsk980.hooks:GSKHooks   # 会写代码的工程师用

# 元信息
author: Lingjing Manufacturing Team
description: 广州数控 GSK 980/25i 系列（Fanuc 0i 兼容方言）
```

### 3.4 模板文件示例（header.j2，Jinja2）

```jinja2
%
O{{ program_number | format('%04d') }} (PROGRAM {{ program_number }} - {{ date_string }})
(POST: GSK 980/25i (Guangzhou CNC))
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G91 G30 X0. Y0.
G00 G90 {{ wcs }} X0. Y0.
G00 G43 Z{{ safe_z_height | fmt }} H00
M03 S{{ default_rpm }}
M08

```

模板上下文由引擎提供：`program_number`、`date_string`、`wcs`（工件坐标系）、`safe_z_height`、`default_rpm`、`fmt`（小数格式化过滤器，等价 `_format_mixin._fmt`）等。**工艺员只需改模板文本，不碰 Python。**

### 3.5 编译与注册流程

```
启动/扫描时（或插件 on_load 时）:
  1. 扫描 postprocessor-plugins/*/dialect.yaml
  2. 解析 extends 继承链（拓扑排序，循环依赖报错）
  3. 加载模板 → 编译为方言方法实现（Jinja2 渲染函数）
  4. 合并参数（自身覆盖父方言，语义同 _deep_merge）
  5. 注册到 DialectRegistry：
     - 纯声明方言 → registry.register(controller_id, 参数化实例工厂)
     - 带钩子方言 → 额外加载 hooks entrypoint
  6. 黄金测试校验：跑同输入 → 输出必须与参考实现一致（仅对内置方言镜像）
```

**注册桥**：现有 `PostProcessorRegistry.register()` 已支持动态注册（registry.py:83-102），新增 `DialectRegistry` 作为其扩展：优先查方言注册表，未命中回退内置类，保证 `load_from_config` 等既有调用方**零改动**。

### 3.6 模板安全

- Jinja2 渲染使用 `Environment(autoescape=False)` + **受限命名空间**（白名单上下文变量 + `fmt` 过滤器），模板不可信时不暴露任意 Python。
- 模板沙箱复用 `app/plugins/skill_loader/sandbox_executor.py` 的子进程隔离思路（已有白名单 builtins 先例），作为远期加固项。
- 校验层前置：`dialect.yaml` 通过 JSON Schema 校验（复用 `plugin.yaml` 的 `config_schema` 模式），非法声明拒绝加载并报可读错误。

---

## 4. 契约与黄金测试（迁移的前提）

### 4.1 黄金测试基线（先行，基于现有框架扩展）

> **现状**：黄金测试框架已存在——`tests/regression/test_postprocessor_golden.py`（9 方言 × 标准序列，全程序逐字节比对 `tests/golden/postprocessor/*.nc`）+ `tests/unit/test_postprocessor.py`（44 个方法级用例）+ `tests/regression/test_gcode_baseline.py`（容差比对）。P0 的工作是**扩展现有框架**，而非从零新建：
> 1. 扩展序列覆盖当前缺失的方法：tapping/boring/threading/groove/subprogram/高精度/RTCP/探针等；
> 2. 补齐边界路径（负数坐标、dwell=0、pecking=False）与错误路径（非法参数 → ValueError）；
> 3. 新黄金文件生成后**人工审阅**再合入。

迁移前必须为全部 9 个方言建立**黄金输出契约测试**：

```
tests/postprocessor/golden/
├── fanuc_0i/
│   ├── case_header.json          # 输入参数 → 期望 NC 输出
│   ├── case_tool_change.json
│   ├── case_arc.json
│   └── ...
├── siemens_840d/...
└── ...
```

- 每条用例 = 固定输入（参数矩阵覆盖正常/边界/错误路径）+ 期望输出字符串。
- 断言**逐字符一致**（NC 输出是机器语言，不允许近似）。
- 声明化后跑同一套测试 → 保证行为零变化。
- 新增方言必须自带黄金用例才允许合入（延续仓库"覆盖率 ≥80% 才可合并"纪律）。

### 4.2 契约稳定性

- `dialect.yaml` schema 标记为 Stable v1.0.0，只允许向后兼容扩展（新字段可选）。
- `extends` 继承链构成事实契约：父方言模板/参数变更必须跑全子方言黄金测试。
- 模板上下文变量清单（3.4 节）作为契约的一部分，变更需更新本文档并跑黄金测试。

---

## 5. 与现有系统的衔接

| 现有资产 | 复用方式 | 改动 |
|----------|----------|------|
| `BasePostProcessor` | 作为方言实例的最终基类（模板编译产物挂接到其方法） | 增加"模板渲染方法"分发，不改既有抽象方法签名 |
| `ConfigLoader`（base/controller merge） | 方言参数合并复用 `_deep_merge` 语义 | 无 |
| `ConfigValidator` | 方言声明合法性 + 参数范围校验 | 扩展校验入口 |
| `ConfigLimiter` | 主轴/进给限幅（NC 安全底线） | 无 |
| `_format_mixin` | `_fmt`/`_comment` 等作为模板过滤器/上下文 | 暴露为模板上下文 |
| `PostProcessorRegistry.register()` | 注册桥底座 | 扩展 `DialectRegistry` 查询优先级 |
| `config/postprocessor_config.yaml` | 保留为全局默认 + 内置方言基线 | 方言插件参数优先级：插件声明 > controllers.<name> > base |
| 插件系统（`app/contracts/plugin.py`） | 远期方言插件可作为真 IPlugin（on_load 时 register） | 本设计不依赖，预留 `hooks` 挂载点 |

### 5.1 前端

- **方言管理页**：列出已注册方言（内置 + 本地插件），显示来源/版本/继承链/健康状态。
- **新建方言向导**：选继承模板 → 填参数（表单由 JSON Schema 驱动）→ 编辑模板（带语法高亮）→ **实时预览 NC 输出**。
- **预览器（杀手锏）**：给定样例刀路输入，渲染当前方言完整 NC 输出，工艺员立刻看到改动效果；改动不合法时给出可读错误（复用 `[错误类型] 具体描述。建议操作：[具体步骤]` 错误格式约定）。
- 前端路由/菜单挂到现有导航组（`src/config/navGroups.ts` 的 plugin 组）。

---

## 6. 实施计划（分阶段）

| 阶段 | 内容 | 产出 | 验收标准 |
|------|------|------|----------|
| **P0 黄金基线** | 已完成（2026-08-19）：扩展现有黄金框架，补齐方法覆盖（tapping/boring/threading/groove/subprogram/高精度/RTCP）+ 边界/错误路径（51 用例） | `tests/regression/test_postprocessor_golden.py` + `tests/golden/postprocessor/*_extended.nc` ×9 + `tests/unit/test_postprocessor_boundary.py` | 172 测试全绿；覆盖正常/边界/错误路径 |
| **P1 引擎扩展** | 已完成（2026-08-19）：`app/postprocessor/dialect/` 包（declaration/compiler/registry）+ 首个声明镜像 KND（4 模板方法） | `app/postprocessor/dialect/` + `postprocessor-plugins/knd_1000_2000_3000/` + `tests/unit/test_postprocessor_dialect.py`（20 用例） | 声明式 KND 输出与内置 KND 逐字符一致（标准序列 + 扩展序列 + golden 文件三重验证）；`load_dialects()` 注册后 `load_from_config` 调用方零改动 |
| **P2 内置方言迁移** | 核心完成（2026-08-19）：6 个 Fanuc 兼容方言中 5 个已声明化（KND/GSK/HNC/Mitsubishi/Fagor），各带完整模板 + 三重黄金一致验证；**hooks 模式完成（遗留项③）**——代码钩子表达模板难表达的复杂逻辑（方法优先级 hooks > 模板 > 基类） | `postprocessor-plugins/<id>/*`（5 方言 × 6-8 模板）+ `tests/unit/test_postprocessor_dialect.py`（37 用例） | 5 个声明镜像输出与内置逐字符一致；hooks 方言可混合模板+hooks+继承（含扩展方法）；fanuc_0i 保留为引擎基类 |
| **P3 前端** | 已完成（2026-08-19）：后端 API（列表/详情/模板读取/NC 预览 + **新建/保存模板/删除写路径**）+ 前端方言管理页 + 实时预览器 + **新建向导 + 模板编辑器** | `app/api/v1/postprocessor_dialects.py` + `src/api/postprocessorDialects.ts` + `src/views/DialectManager.vue` + `tests/api/test_postprocessor_dialects.py`（23 用例）+ `src/views/__tests__/DialectManager.test.ts`（10 用例） | **工艺员零代码加方言完整闭环**：新建（选继承 → 生成参数化骨架模板）→ 编辑模板 → 实时预览 → 删除；后端 92 测试 + 前端 10 测试全绿 |
| **P4 插件接线** | 核心完成（2026-08-19）：方言插件暴露到统一插件市场（plugin_type=postprocessor，id 前缀 `dialect:`）+ **init_plugin_system 安全接线（遗留项②）**——main.py startup Step 5 无参初始化（0 插件，不触发 torch 依赖），shutdown 5.5 清理；`get_plugin_manager()` 不再抛 RuntimeError，插件 API 返回真实数据 | `app/api/v1/plugins.py`（`_scan_dialect_plugins`）+ `app/main.py` + `tests/api/test_dialect_plugins_market.py`（3 用例）+ `tests/unit/test_plugin_system_wiring.py`（4 用例） | `/api/v1/plugins/marketplace` 显示 5 个方言插件真实条目；插件系统接线后市场 API code=0 返回真实数据（修复审计发现的"吞异常返回空"） |

> P0 是**不可跳过的前提**（契约即负债，迁移前必须有行为基线）。P1-P2 每步独立 PR，符合仓库"完整实现、测试同步、不留 TODO"纪律。

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 模板渲染与 f-string 输出不一致 | NC 行为漂移 | P0 黄金测试先行，逐字符断言 |
| Jinja2 引入依赖与安全面 | 模板注入/逃逸 | 受限命名空间 + 白名单上下文；远期子进程沙箱 |
| Heidenhain 等复杂方言难以纯声明化 | 迁移卡壳 | 保留 `hooks` 代码钩子逃生舱；复杂方言允许"半声明半钩子" |
| 参数优先级混乱 | 方言行为不可预期 | 明确优先级：插件声明 > controllers.<name> > base；Validator 前置校验 |
| 契约膨胀 | 维护负担 | dialect.yaml schema 严格 Stable 纪律，新字段必须向后兼容 |
| 插件系统未接线 | 方言插件无法走统一市场 | P4 单独工作项；P1-P3 不依赖插件系统，方言经独立目录扫描即可用 |

---

## 8. 相关文档

- [ADR-005-核心架构契约设计](../adr/ADR-005-核心架构契约设计.md)
- [core-contracts-design.md](core-contracts-design.md)
- [config/postprocessor_config.yaml](../../config/postprocessor_config.yaml)
- [app/postprocessor/](../../engineering/python/app/postprocessor/)（base.py / registry.py / config_loader.py / fanuc.py / gsk.py 等）
- 插件系统审计结论（.dsh-memory/2026-08-19.md 22:34 条）

---

## 9. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-08-19 | 初始版本（基于插件化战略讨论 + 代码审计实证） | Agent |
| 2026-08-19 | P0 完成：扩展黄金框架（*_extended.nc ×9）+ 边界/错误路径测试（51 用例）；修正方言数为 9 | Agent |
| 2026-08-19 | P1 完成：app/postprocessor/dialect/ 包（declaration/compiler/registry）+ KND 声明镜像原型 + 20 个方言测试 | Agent |
| 2026-08-19 | P2 核心完成：新增 GSK/HNC/Mitsubishi/Fagor 四个声明镜像（共 5 个），32 个方言测试全绿；Siemens/Heidenhain/XM100 决定留核心 + hooks 逃生舱 | Agent |
| 2026-08-19 | P3 完成：后端方言 API（列表/详情/模板/预览，13 测试）+ 前端 DialectManager.vue（6 测试）+ 路由/导航/i18n；预览序列抽到生产代码 preview_sequence.py | Agent |
| 2026-08-19 | P4 核心完成：方言插件暴露到统一插件市场（plugin_type=postprocessor，3 测试）；init_plugin_system 完整接线列为后续独立工作项 | Agent |
| 2026-08-19 | 遗留项①完成：方言新建向导 + 模板编辑保存 + 删除写路径（后端 10 用例 + 前端 4 用例）；骨架模板参数化（program_number/safe_z/转速/坐标系转 Jinja2 变量），保存后自动刷新预览 | Agent |
| 2026-08-19 | 遗留项⑤完成：方言参数（params）读写——GET 返回有效配置（base 深合并方言层）+ PUT 保存方言 params；编译器补上 params 注入缺口（顶层标量提升为构造参数 + 其余深合并 config），保存参数后编译实例反映新值（后端 4 用例 + 前端 3 用例） | Agent |
| 2026-08-19 | 遗留项③完成：hooks 模式实现——声明 `hooks: module.path:ClassName` 加载代码钩子，format_* 方法覆盖基类/模板（优先级 hooks > 模板 > 基类），可提供基类 MRO 之外的新方法（6 个测试用例，含错误路径）；Siemens/Heidenhain/XM100 这类复杂方言现在可用「模板 + hooks」声明化表达 | Agent |
| 2026-08-19 | 遗留项②核心完成：init_plugin_system 安全接线——main.py startup Step 5 无参初始化（空 plugin_dirs → 0 插件，不触发 torch 依赖；失败仅告警不阻断启动）+ shutdown 5.5 清理；修复「插件系统从未接电、get_plugin_manager 抛 RuntimeError、插件 API 吞异常返回空」的审计发现（4 接线测试 + 市场真实数据验证） | Agent |
