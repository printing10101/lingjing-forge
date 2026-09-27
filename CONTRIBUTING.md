# 贡献指南 (CONTRIBUTING.md)

欢迎为**灵境制造 (Virtual Realm Manufacturing)** 项目做出贡献！本文档将帮助您了解如何参与开发、提交代码以及遵守项目规范。

---

## 目录

- [环境搭建](#环境搭建)
  - [前端环境](#前端环境)
  - [Tauri 桌面端环境](#tauri-桌面端环境)
  - [Python 后端环境](#python-后端环境)
  - [AI 模型环境](#ai-模型环境)
- [开发流程](#开发流程)
- [代码规范](#代码规范)
  - [前端 (TypeScript / Vue)](#前端-typescript--vue)
  - [Python](#python)
  - [Rust](#rust)
- [测试](#测试)
  - [前端单元测试](#前端单元测试)
  - [Python 单元测试](#python-单元测试)
  - [前端组件测试](#前端组件测试)
- [PR 规范](#pr-规范)
- [Bug 报告](#bug-报告)
- [项目结构说明](#项目结构说明)

---

## 环境搭建

### 前端环境

> **要求**：Node.js >= 18，pnpm 包管理器

```bash
# 安装 pnpm（如尚未安装）
npm install -g pnpm

# 在项目根目录安装前端依赖
pnpm install

# 启动开发服务器（http://localhost:1420）
pnpm dev

# TypeScript 类型检查
pnpm type-check
```

> **注意**：本项目使用 `pnpm` 而非 `npm` 作为包管理器，`pnpm-lock.yaml` 已纳入版本控制。`package.json` 中配置了 `"packageManager": "pnpm@9.x"`。

### Tauri 桌面端环境

> **要求**：Rust >= 1.70，Tauri CLI

```bash
# 安装 Rust（如尚未安装）
# Windows: 访问 https://rustup.rs/ 下载安装器
# macOS/Linux: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 验证 Rust 版本
rustc --version    # 应 >= 1.70.0

# 安装 Tauri CLI
cargo install tauri-cli --version "^2.0"

# 验证 Tauri CLI
cargo tauri --version

# 启动 Tauri 桌面应用
cargo tauri dev
```

> **Windows 额外要求**：需要安装 WebView2 Runtime 和 Visual Studio Build Tools（含 C++ 工作负载）。详见 [Tauri 官方文档](https://v2.tauri.app/start/prerequisites/)。

### Python 后端环境

> **要求**：Python >= 3.10，建议使用虚拟环境

```bash
# 创建虚拟环境（推荐）
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 安装 Python 依赖（在项目根目录执行）
pip install -r requirements.txt

# 启动后端服务（端口 8765）
cd python
python main.py
```

主要依赖包括：FastAPI、PyTorch、NumPy、scikit-learn、matplotlib、PyYAML、pandas 等。完整列表见 `requirements.txt`。

### AI 模型环境

灵境制造使用 **Ollama** 在本地运行大语言模型，以及自研的 **LNN（液体神经网络）** 模型。

```bash
# 1. 安装 Ollama
# 官方下载页面: https://ollama.com/download
# Windows/macOS: 下载安装包
# Linux: curl -fsSL https://ollama.com/install.sh | sh

# 2. 拉取推荐模型（qwen2.5-coder:7b 用于代码生成和工艺规划）
ollama pull qwen2.5-coder:7b

# 3. 验证模型可用性
ollama list
# 应显示 qwen2.5-coder:7b

# 4. 测试推理
ollama run qwen2.5-coder:7b "你好，请介绍你自己"
```

> **注意**：首次启动后端服务时，LNN 模型需要从 `python/models/lnn/` 加载权重文件（通过 Git LFS 管理，请确保已执行 `git lfs pull`）。

---

## 开发流程

本项目采用标准的 **GitHub Flow** 工作流。所有贡献需通过 Pull Request 提交。

### 标准开发流程

```bash
# 1. Fork 本项目到您的 GitHub 账号
#    点击仓库页面右上角的 "Fork" 按钮

# 2. 克隆您的 Fork
git clone git@github.com:<您的用户名>/Virtual-Realm-Manufacturing.git
cd Virtual-Realm-Manufacturing

# 3. 添加上游远程仓库
git remote add upstream git@github.com:printing10101/Virtual-Realm-Manufacturing.git

# 4. 同步上游最新代码
git fetch upstream
git checkout main
git merge upstream/main

# 5. 创建功能分支（命名规范见下方）
git checkout -b feature/<功能简述>

# 6. 开发并提交代码
git add <修改的文件>
git commit -m "feat(module): 添加新功能描述"

# 7. 推送分支到您的 Fork
git push origin feature/<功能简述>

# 8. 在 GitHub 上创建 Pull Request
#    从您的 Fork 分支 → 上游 main 分支
```

### 分支命名规范

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能开发 | `feature/nc-simulation` |
| `fix/` | Bug 修复 | `fix/collision-detection` |
| `docs/` | 文档更新 | `docs/api-reference` |
| `refactor/` | 代码重构（不改变功能） | `refactor/toolpath-parser` |
| `perf/` | 性能优化 | `perf/lnn-inference` |
| `test/` | 测试补充 | `test/postprocessor` |
| `chore/` | 构建/工具/配置变更 | `chore/update-deps` |

### Commit Message 规范

本项目已配置 **commitlint** 强制检查。提交信息必须遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <description>

[可选的详细描述]

[可选的脚注]
```

**type 类型**：`feat` | `fix` | `docs` | `style` | `refactor` | `perf` | `test` | `chore` | `build` | `ci`

**示例**：

```bash
git commit -m "feat(simulation): 添加AABB碰撞检测引擎"
git commit -m "fix(constraints): 修复TC4钛合金切削速度约束计算"
git commit -m "docs(readme): 添加Git LFS使用指南"
git commit -m "test(database): 新增材料数据库查询单元测试"
```

> **提示**：如果您提交时遇到 commitlint 错误，请根据提示信息调整提交信息格式。可以使用 `git commit --no-verify` 跳过检查（**不推荐**，仅用于紧急情况）。

---

## 代码规范

项目使用自动化工具强制执行代码规范。**提交前会自动运行 lint-staged 检查**（通过 husky 钩子），未通过检查的代码将被阻止提交。

> **编辑器配置**：项目根目录包含 `.editorconfig` 文件，统一管理缩进风格、换行符、字符编码等基础格式。请在 IDE 中安装 **EditorConfig 插件**（VS Code: `EditorConfig for VS Code`，JetBrains 系列自带支持），确保跨编辑器格式一致。

### 前端 (TypeScript / Vue)

| 工具 | 用途 | 配置文件 |
|------|------|---------|
| ESLint | 代码质量检查 | `.eslintrc.cjs` |
| Prettier | 代码格式化 | `package.json` 中的 `prettier` 配置 |
| vue-tsc | TypeScript 类型检查 | `tsconfig.json` |

```bash
# 运行 ESLint 检查并自动修复
pnpm lint

# 运行 Prettier 格式化
pnpm format

# TypeScript 类型检查
pnpm type-check

# 常见问题
# Q: ESLint 报 "Component name should always be multi-word"
#    在 .eslintrc.cjs 的 vue/multi-word-component-names 中添加例外
# Q: Prettier 格式化后代码看起来不对
#    检查 VSCode 是否设置了默认格式化工具为 Prettier
```

### Python

| 工具 | 用途 | 配置文件 |
|------|------|---------|
| Black | 代码格式化 | `pyproject.toml`（行宽 88） |
| Ruff | 快速代码检查 + 格式化 | `pyproject.toml` |
| Flake8 | 代码风格检查（通过 pre-commit） | `.flake8` |

```bash
# 格式化所有 Python 文件
black python/

# Ruff 检查并修复
ruff check --fix python/

# 仅检查（不修复）
ruff check python/

# Flake8 检查（pre-commit 中自动运行）
flake8 python/

# 常见问题
# Q: Black 格式化与 Flake8 冲突
#    本项目已配置 max-line-length=88 以兼容 Black
# Q: 导入顺序报错
#    使用 ruff check --fix 自动排序导入（isort 规则已内置于 Ruff）
```

### Rust

| 工具 | 用途 |
|------|------|
| `cargo fmt` | 代码格式化 |
| `cargo clippy` | 静态分析 + 最佳实践检查 |

```bash
# 在 src-tauri/ 目录下执行

# 格式化 Rust 代码
cargo fmt

# Clippy 静态检查（推荐包含所有警告组）
cargo clippy -- -W clippy::all -W clippy::pedantic

# Clippy 检查 + 自动修复简单问题
cargo clippy --fix -- -W clippy::all

# 常见问题
# Q: clippy 报 "unnecessary_cast" 等建议
#    运行 cargo clippy --fix 自动修复
# Q: 编译时间过长
#    可先运行 cargo check（仅检查语法，不生成二进制文件）
```

---

## 测试

**提交 PR 前必须通过所有测试。** CI 流程会自动运行测试，未通过的 PR 无法合并。

### 前端单元测试

```bash
# 运行所有前端测试
pnpm test:run

# 监听模式（开发时使用）
pnpm test

# 运行单个测试文件
pnpm vitest src/components/__tests__/ErrorNotification.spec.ts

# 生成覆盖率报告
pnpm vitest run --coverage
```

测试框架：**Vitest** + **@vue/test-utils**。测试文件与源文件同目录（`__tests__/` 子目录或 `*.spec.ts` 后缀）。

### Python 单元测试

```bash
# 运行所有 Python 测试
pytest tests/ -v

# 运行指定模块测试
pytest tests/test_simulation.py -v
pytest tests/test_database.py -v
pytest tests/test_postprocessor.py -v

# 运行测试并生成覆盖率报告
pytest tests/ -v --cov=python/app --cov-report=html

# 仅运行快速测试（跳过耗时测试）
pytest tests/ -v -m "not slow"

# 测试文件组织结构
tests/
├── unit/              # 单元测试（纯函数、模块）
├── api/               # API 集成测试
├── validation/        # 验证模块集成测试
├── test_simulation.py # NC仿真测试
├── test_database.py   # 刀具/材料数据库测试
├── test_postprocessor.py
├── test_process_planning.py
├── test_error_taxonomy.py
├── test_perf_benchmark.py
└── conftest.py        # 共享 fixtures
```

> **覆盖率要求**：核心模块（`core/`、`database/`、`validation/`、`process_planning/`）代码覆盖率应 >= 90%。

### 前端组件测试

```bash
# 运行所有前端组件测试（Vitest）
npx vitest run

# 运行指定测试文件
npx vitest run tests/ExampleGallery.spec.ts

# 交互式 watch 模式
npx vitest

# 查看测试覆盖率
npx vitest run --coverage
```

前端测试基于 **Vitest**，测试文件位于 `tests/` 目录下（`*.spec.ts`）。

---

## PR 规范

### PR 标题格式

PR 标题必须遵循 Conventional Commits 格式（与 commit message 相同）：

```
<type>(<scope>): <简述>
```

**示例**：
- `feat(simulation): 实现NC代码刀具路径碰撞检测系统` 
- `fix(postprocessor): 修复Fanuc圆弧插补R值计算` 
- `docs(contributing): 新增贡献指南` 
- `更新了一些代码` （缺少 type 和 scope）

### PR 描述模板

创建 PR 时请包含以下内容：

```markdown
## 变更概述
简要描述本次变更的内容和目的

## 相关 Issue
Fixes #123 或 Resolves #123

## 变更类型
- [ ] 新功能 (feat)
- [ ] Bug 修复 (fix)
- [ ] 文档更新 (docs)
- [ ] 重构 (refactor)
- [ ] 测试 (test)
- [ ] 其他

## 测试
- [ ] 已通过所有单元测试
- [ ] 已添加新的测试用例
- [ ] 已通过前端组件测试（如有需要）

## 截图/录屏（如适用）
```

### 关联 Issue

使用 GitHub 关键字自动关联 Issue：

- `Fixes #<编号>` — 合并后自动关闭 Issue
- `Resolves #<编号>` — 同上
- `Related to #<编号>` — 仅关联，不关闭

### 合并要求

- PR 标题符合 Conventional Commits 格式
- 所有 CI 检查通过（lint + test + type-check）
- 至少一位指定 Reviewer 审批通过
- 与 `main` 分支无冲突
- 禁止直接推送到 `main` 分支

---

## Bug 报告

发现 Bug 时，请通过 **GitHub Issues** 提交报告：

### 提交步骤

1. 访问 [Issues 页面](https://github.com/printing10101/Virtual-Realm-Manufacturing/issues)
2. 点击 "New Issue"，选择 **Bug Report** 模板
3. 填写以下必填信息：

### Bug 报告模板

```markdown
### Bug 描述
简要描述遇到的问题

### 复现步骤
1. 打开 '...'
2. 点击 '...'
3. 滚动到 '...'
4. 观察错误

### 预期行为
应该发生什么

### 实际行为
实际发生了什么

### 环境信息
- 操作系统: [如 Windows 11 23H2]
- 浏览器: [如 Chrome 126]
- 应用版本: [如 v1.8.0]
- Python 版本: [如 3.11.0]
- Node.js 版本: [如 20.12.0]

### 截图/录屏
（如有，请附上）

### 错误日志
```
（粘贴相关的错误日志）
```
```

### Bug 报告建议

- **先搜索**：检查是否已有相同 Issue
- **附截图**：截图比文字描述更直观
- **贴日志**：`logs/` 目录下的相关日志文件
- **可复现**：提供最小复现步骤，帮助开发者快速定位

---

## 项目结构说明

```
Virtual-Realm-Manufacturing/
├── src/                          # 前端源代码（Vue 3 + TypeScript）
│   ├── components/               # Vue 组件（ErrorNotification 等）
│   ├── views/                    # 页面视图
│   ├── stores/                   # Pinia 状态管理
│   ├── router/                   # Vue Router 路由配置
│   ├── api/                      # API 请求封装
│   ├── composables/              # Vue 组合式函数
│   └── styles/                   # 全局样式（SCSS）
│
├── src-tauri/                    # Tauri 桌面应用（Rust）
│   ├── src/
│   │   ├── main.rs               # 应用入口
│   │   ├── commands/             # Tauri 命令（IPC 接口）
│   │   ├── models.rs             # 数据模型
│   │   ├── storage.rs            # 本地存储
│   │   └── auth.rs               # 认证模块
│   ├── Cargo.toml                # Rust 依赖配置
│   └── tauri.conf.json           # Tauri 配置
│
├── python/                       # Python 后端
│   ├── app/
│   │   ├── main.py               # FastAPI 服务入口
│   │   ├── config.py             # 全局配置管理
│   │   ├── ai/lnn/               # LNN 模型（训练/推理/量化）
│   │   ├── api/v1/               # REST API 路由
│   │   ├── core/                 # 核心基础（异常、响应、权限等）
│   │   ├── cad/                  # CAD 生成与工艺路由
│   │   ├── postprocessor/        # CNC 后处理器（Fanuc/Siemens/Heidenhain）
│   │   ├── database/             # 刀具/材料/机床数据库
│   │   ├── process_planning/     # 装夹方案与工序排序
│   │   ├── simulation/           # NC 刀具路径仿真与碰撞检测
│   │   ├── validation/           # 3D 重建精度验证
│   │   ├── benchmarks/           # 基准测试与性能检测
│   │   ├── rag/                  # RAG 知识库检索
│   │   └── services/             # 业务服务（刀具磨损预测等）
│   ├── tests/                    # Python 测试
│   ├── models/lnn/               # LNN 训练模型权重
│   └── data/uniwear/             # UniWear 刀具磨损数据集
│
├── docs/                         # 项目文档
│   ├── 用户手册.md
│   ├── 开发指南.md
│   └── benchmarks/               # 基准实验报告
│
├── config/                       # 运行时配置文件
├── tests/                        # Vitest 前端组件测试 + benchmark 基线
├── .github/workflows/            # GitHub Actions CI/CD
├── requirements.txt              # Python 依赖
├── package.json                  # 前端依赖与脚本
└── README.md                     # 项目主文档
```

---

## 许可证

本项目采用 **Apache License 2.0** 许可证。参与贡献即表示您同意将您的代码以相同许可证授权,并授予相应的专利使用权。详见 [LICENSE](LICENSE) 文件。

- **商业使用**:允许,无需额外授权
- **修改与分发**:允许,需保留版权与许可证声明
- **专利授权**:贡献者自动授予必要的专利使用权
- **商标使用**:本许可证不授予商标使用权

---

## 架构规则（Architecture Rules · 2026-08-03）

以下规则由 `tests/architecture/` 强制执行（CI 阻断）。数据来源：全量架构审计。

### 依赖方向
- **API 层不得直接 `import app.database` / `app.models`**（仅允许 `app.models.schemas` 类型导入）。数据访问经 `app.infrastructure/repositories/`。
- **单例工厂仅从 `app.dependencies` 导入**：禁止 `from app.X import get_*`（白名单仅 `app.dependencies`）。

### 契约定义
- **契约定义于被消费的领域包**，禁止新建顶层共享目录。范式：`chatter_prediction/_types.py`。

### 代码体积
- 生产 `.py` ≤800 行；生产 `.vue` ≤800 行。

### 类型与 Lint
- 禁止新增 `# noqa`（已全局清理）。新增 `# type: ignore` 需 PR 描述说明理由。

### 路由注册
- 全部通过 `app/router_registry.py` 挂载。`main.py` 禁止路由装饰器。
