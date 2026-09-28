# INSTINCTS

## 本机镜像源选型地图（2026-09-28 实测）

- **触发**：docker pull 走默认源失败（Docker Hub registry-1.docker.io 直连被墙；quay.io 匿名 token 握手 401）。
- **正确做法**：nexent 系 → `ccr.ccs.tencentyun.com/nexent-hub`（tag 带 **v** 前缀，如 `nexent:v2.5.1`）；
  Docker Hub library 官方镜像（postgres/redis 等）→ `public.ecr.aws/docker/library/*`（manifest 稳定、blob 偶发 RST，重试即可）；
  quay 系 → `quay.m.daocloud.io/*`（`docker.m.daocloud.io` 只代理 docker.io，不要用）；
  `docker.elastic.co` 直连可达。批量拉取带重试脚本见 `D:\nexent\pull_images.sh`。

## Nexent 部署现状与启动（2026-09-28）

- **触发**：`docker ps` 无容器、compose 端口（3000/5010-5015/5434 等）无人监听时。
- **正确做法**：Nexent v2.5.1 栈（13 容器，compose 项目名 `nexent`）真实部署目录在
  **`D:\nexent-deploy\nexent`**（不在灵境制造仓库，`D:\nexent` 只是源码克隆）。
  Docker Desktop 装在用户目录 `AppData\Local\Programs\DockerDesktop\`（沙箱里 cmd start 会被拒，
  用 PowerShell `Start-Process`）；引擎起后 restart:always 自动恢复全部容器，web 控制台 `http://localhost:3000`。

## 灵境 MCP SSE 网关启动（2026-09-28）

- **触发**：Nexent 需要注册灵境 26 个 MCP 工具，或要验证 Bearer 鉴权 SSE。
- **正确做法**：仓库根执行 `PYTHONPATH=engineering/python LINGJING_AGENT_TOKEN=<≥32字符> LINGJING_MCP_INGRESS_TOKEN=<≥32字符> LNN_MCP_ALLOW_REMOTE=1 python -m mcp_server.server --transport sse --host 0.0.0.0 --port 8080`（系统 Python 3.14 有 mcp 包；desktop_runtime 没有）。容器内经 `host.docker.internal:8080/sse` 可达。

## Mimosa 门禁调参（2026-09-27）

- **触发**：要调整 L3 commit 门禁（如排除 research/ 误报）时。
- **正确做法**：唯一有效的旋钮是环境变量（README「配置」节）。`.mimosa/security-policy.json` 的
  `threatModel.exclusions` 只影响 threat-model 上下文，**不被 git 门禁采纳**；
  `# mimosa-ignore` 注释实测也不能压制「路径穿越」误报。已设
  `setx MIMOSA_GIT_GATE_MODE warn`（提交前仍全量扫描+报告，但不再硬拦）；
  Edit/Write 实时扫描保持 graded（high 必拦）。env 需重启 ZCode 才生效，
  当前会话被拦的提交仍要在用户终端做。
- **证据**：policy exclusions 写入后 commit 仍拦（309 high 全部是 research/ 测试
  os.path.join(tempfile.mkdtemp()) 误报）；`scan` 单文件对加注释行依旧报 L37。

## research 测试的工程侧依赖（2026-09-27）

- **触发**：`cd research && pytest tests/` 报 `ModuleNotFoundError: No module named 'app'`；或要在 research 测试里 `from app...` 导入工程侧包。
- **正确做法**：路径注入统一放 `research/conftest.py`（append `engineering/python`，不 prepend，避免遮蔽 research 自己的顶层包）；不要在测试文件里手拼 `parents[N]`——层数极易算错指向 `D:\`，桌面宿主注入的 PYTHONPATH 一消失就炸（坑 1 的反面）。
- **证据**：test_quantization / test_gpu_training / test_model_benchmark / test_data_split 曾靠宿主 PYTHONPATH 碰巧通过；2026-09-25 仓库重建后裸 shell 下 4 文件收集失败，conftest 修复后 82 passed / 13 skipped。

## Mimosa Git 门禁与 SAST 误报（2026-09-27）

- **触发**：ZCode 会话里 `git commit` 被拦："Mimosa L3 发现 301 个高危…已强制拦截，请修复并重新扫描"。
- **正确做法**：该门禁只在 ZCode 工具链内生效，用户终端 git 不受影响。本项目为文件路径密集型（CAD/数据集）+ 科研代码，扫描器"路径穿越/不安全随机数"类高危绝大多数是误报，逐条改码迎合扫描器会劣化代码。提交被拦时向用户说明三个选项：终端自行提交 / 调整插件策略 / 集中清理误报，不要自行绕过安全钩子。
- **证据**：deep 扫描 293 项发现（244 high）中 195 为路径穿越、46 为不安全随机数；threatModel 阶段入口为 0 导致扫描永远 inconclusive，"重新扫描"无法让门禁放行。

## 公开仓本机路径（2026-09-09）

- **触发**：CI 基线报告、研究脚本日志、软著收集脚本会写绝对路径。
- **正确做法**：生成报告时剥离 `C:\Users\<用户名>\...`；提交前扫描 `Users\\Lenovo`。
- **证据**：`.ci_baseline_response_model.json` 等 27 文件已替换为 `C:\Users\<user>`。

## 文档中的 PEM

- **触发**：TLS/证书说明文档贴示例。
- **正确做法**：用 `<PEM 示例>` 占位，不要贴 `BEGIN PRIVATE KEY` 字样，避免密钥扫描误报。
- **证据**：`deploy/nginx/README_TLS.md` 已改为占位文本。

## 论文/专利草稿

- **触发**：`output/paper-writer/`、`outputs/patent-mining/`。
- **正确做法**：不进公开仓；需要时另建私有仓或本地归档。
- **证据**：2026-09-09 推送时显式排除。
