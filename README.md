# 灵境制造（Lingjing Manufacturing）

灵境制造：**桌面级制造物理 AI**。AI 全流程生成「图纸 → 3D 模型 → 工艺规划 → NC 代码」，体素仿真强制校验、DNC 下发硬闸兜底；推理与数据全程本地，工艺资产不出厂。

**图纸进，代码出**——每一行 G 代码都经过物理校验才上机，每一份数据都留在你自己的电脑里。内置本地/云可插拔 LLM 网关、LNN（液态神经网络）铣削颤振预测、刀具磨损预测与 RAG 工艺知识库。

技术栈：Tauri (Rust) + Vue3 + Python/FastAPI | 开源协议：Apache-2.0

---

## 快速开始

> 支持三种安装方式，按你的场景任选其一。

### 方式一：桌面端（Windows 推荐）

1. 前往 [GitHub Releases](https://github.com/printing10101/Virtual-Realm-Manufacturing/releases) 下载最新安装包（`lingjing_<版本>_x64-setup.exe`）。
2. 双击安装，启动即用 —— 安装包已内置 Python 后端，无需预装任何环境。

> 中国大陆下载加速镜像见 [国内部署指南](docs/国内部署指南.md)。

### 方式二：服务端一键安装（Linux / macOS / WSL2）

零前置依赖（仅 git），脚本自动安装 Python 3.12 与全部依赖，**无需 sudo**：

```bash
# 官方源
curl -fsSL https://raw.githubusercontent.com/printing10101/Virtual-Realm-Manufacturing/main/deploy/install.sh | bash

# 中国大陆（GitHub 代理 + 阿里云 PyPI 加速）
LINGJING_CN=1 curl -fsSL https://raw.githubusercontent.com/printing10101/Virtual-Realm-Manufacturing/main/deploy/install.sh | bash
```

安装完成后：

```bash
source ~/.bashrc        # 刷新 PATH
lingjing doctor         # 自检安装状态
lingjing start          # 启动服务 → http://localhost:8765
```

其他命令：`lingjing stop` / `restart` / `status` / `update` / `uninstall`

> 完整参数与说明见 [安装指南](docs/user-guide/安装指南.md)。

### 方式三：Docker（服务器 / 工厂部署）

```bash
# 轻量单机版（SQLite，无需外部数据库）
cp .env.sqlite.example .env.sqlite
docker compose -f docker-compose-sqlite.yml --env-file .env.sqlite up -d

# 完整版（PostgreSQL + Redis + TDengine + 监控）
cp .env.example .env    # 编辑填入密码与密钥
docker compose --profile full up -d
```

> 国内镜像源与离线部署见 [国内部署指南](docs/国内部署指南.md)。

---

## 平台支持

| 平台 | 推荐方式 | 说明 |
|---|---|---|
| Windows 10/11 | 桌面安装包 | 开箱即用，无需 WSL/Docker |
| Linux 服务器 | 一键脚本 或 Docker | 生产环境建议 Docker 完整版 |
| macOS | 桌面安装包 / 一键脚本 | 桌面包 M 系列部分模型无 MPS 后端 |
| WSL2 | 一键脚本 | 与 Linux 一致 |

---

## 核心能力

- **图纸解析**：DXF / STEP 导入 → 三维重建
- **工艺规划**：自动识别孔/槽/面特征，生成加工工艺
- **NC 代码**：9 种内置后处理器 + YAML 方言包（Fanuc / Siemens / Heidenhain 等）
- **AI 预测**：LNN/LTC 铣削颤振预测、刀具磨损预测（本地推理，数据不出设备）
- **智能交互**：NL2CAD 自然语言建模、RAG 工艺知识库问答
- **车间集成**：OPC UA 机床数据采集、MES/ERP 对接（可选）

## 文档

- [安装指南](docs/user-guide/安装指南.md)
- [国内部署指南](docs/国内部署指南.md)
- [快速入门](docs/user-guide/快速入门.md)
- [安全须知](docs/user-guide/安全须知.md)
- [项目概览](PROJECT_OVERVIEW.md)

## 参与贡献

见 [贡献指南](CONTRIBUTING.md)。版本一致性由 `scripts/version_sync.py` 保障（CI 门禁）。

## Datasets

大型数据集不随仓库分发（减小体积）：
- `research/datasets/uniwear/uniwear/nuaa_orthogonal_bundle_high_resolution.csv`（56MB）与 `phm2010_bundle_high_resolution.csv`（12MB）已移出版本库，PHM2010 为公开数据集可从官方渠道获取；uniwear 数据请参考论文说明或联系作者。
- 其余小型数据与实验代码仍在库内。
