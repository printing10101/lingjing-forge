# Security Policy

## Supported Versions

灵境制造(Virtual Realm Manufacturing)目前对以下版本提供安全更新支持:

| Version | Supported          |
| ------- | ------------------ |
| 2.5.x   | :white_check_mark: |
| 2.4.x   | :white_check_mark: |
| < 2.4   | :x:                |

> 旧版本仅提供重大安全漏洞的修复建议,不保证提供补丁。建议始终使用最新稳定版本。

---

## Reporting a Vulnerability

**请勿通过公开 Issue 报告安全漏洞。**

我们高度重视本项目及其用户的安全。如果您发现安全漏洞,请通过以下任一方式**私密**报告:

### 推荐方式:GitHub Security Advisory

1. 前往仓库主页 [github.com/printing10101/Virtual-Realm-Manufacturing](https://github.com/printing10101/Virtual-Realm-Manufacturing)
2. 点击 **Security** 标签
3. 选择 **Report a vulnerability**
4. 填写漏洞详情(影响范围、复现步骤、建议修复方案)

GitHub Security Advisory 支持私密协作,在补丁发布前不会公开漏洞详情。

### 备选方式:邮件

若无法使用 GitHub Security Advisory,可发送加密邮件至仓库维护者。请在主题行加上 `[SECURITY]` 前缀。

### 报告内容

为帮助我们快速定位与修复,请尽量包含以下信息:

- **漏洞类型**(SQL 注入 / XSS / RCE / 认证绕过 / 路径遍历 / SSRF 等)
- **受影响模块**(后端 API / 前端 / Tauri IPC / LNN 引擎 / RAG / 知识图谱 / DNC / 后处理器等)
- **复现步骤**(最小可复现场景)
- **影响范围**(数据泄露 / 代码执行 / 拒绝服务 / 权限提升)
- **环境信息**(操作系统、Python/Node.js/Rust 版本、应用版本)
- **建议修复方案**(若有)

---

## Response Process

维护团队承诺按以下流程响应:

| 阶段 | 时间窗口 | 动作 |
|------|---------|------|
| 确认收到 | 48 小时内 | 维护者确认收到报告并指定对接人 |
| 初步评估 | 7 天内 | 评估漏洞严重性(CVSS 评分)与影响范围 |
| 修复方案 | 30 天内 | 提供修复方案或临时缓解措施 |
| 补丁发布 | 90 天内 | 高危漏洞优先发布补丁版本 |
| 公开披露 | 补丁发布后 14 天 | 在 GitHub Security Advisory 公开披露 |

**严重程度分级**(参考 CVSS v3.1):

- **Critical (9.0–10.0)**:远程代码执行、认证绕过、数据完全泄露
- **High (7.0–8.9)**:权限提升、敏感数据泄露
- **Medium (4.0–6.9)**:有限信息泄露、有限拒绝服务
- **Low (0.1–3.9)**:信息泄露、非敏感配置暴露

---

## Scope

本安全策略覆盖灵境制造项目的所有组件:

| 层 | 组件 | 安全关注点 |
|----|------|-----------|
| 前端 | Vue 3 / TypeScript / Three.js | XSS、CSP 违规、IPC 越权 |
| 桌面外壳 | Tauri 2 / Rust | IPC 命令越权、sidecar 注入、文件系统越权 |
| 后端 | FastAPI / Python | 注入、认证绕过、SSRF、路径遍历 |
| AI 内核 | LNN / LLM Gateway / RAG | 模型反序列化、prompt injection、向量库注入 |
| 知识图谱 | extractor / query_api | LLM 输出注入、查询越权 |
| 后处理器 | 11 控制器语法树 | 代码注入(后处理输出被恶意构造) |
| DNC | MTConnect / OPC UA / MES | 协议越权、未授权控制指令 |
| 数据持久化 | SQLite / ChromaDB / LFS | SQL 注入、模型文件篡改 |

---

## Security Best Practices for Deployers

灵境制造设计上以"数据不出厂"为安全底线,部署者应遵循以下实践:

### 令牌管理

- 生产环境**必须**使用 `LNN_TOKEN` 环境变量,不要依赖自动生成的 `.lnn_token`
- 令牌定期轮换(建议 30–90 天)
- 不同部署实例使用独立令牌
- CI/CD 通过 GitHub Secrets 注入

### 网络隔离

- 后端 FastAPI 服务(端口 8765)**不应**直接暴露到公网
- 仅通过 Tauri 桌面应用本地访问,或部署在内网受控环境
- 如需远程访问,使用反向代理 + TLS + 客户端证书

### LLM 模型安全

- 本地 LLM(Ollama / llama.cpp)推荐运行在独立进程,沙箱隔离
- 云端 API 调用需评估数据敏感性,敏感工艺数据**不应**发送到云端 LLM
- `useSovereigntySettings` composable 会监控数据流向,建议保持开启

### 模型文件完整性

- LNN 权重文件(Git LFS 管理)应通过 SHA256 校验完整性
- 自训练模型权重应在受控环境生成,避免供应链污染
- 模型文件权限设置为只读(644)

### CNC 安全

- **永远不要**直接将 AI 生成的 NC 代码上机,必须经过 CAM 工程师审核
- 后处理器输出的 G 代码应在仿真环境中验证刀具路径
- DNC 集成启用前,确保 MTConnect/OPC UA 服务端有认证机制

---

## Known Security Considerations

### 设计上的安全特性

- **全栈本地化**:LLM / 数据库 / 模型权重 / 工艺数据不出本地
- **Bearer Token 认证**:4 级权限(R/W/T/A)
- **Provider Gateway 软依赖**:关键 LLM 模块有规则回退,云端 API 不可用时不影响核心功能
- **Tauri 2 CSP**:Content Security Policy 已配置 `ipc:` 与 `http://ipc.localhost`,防止 XSS 通过 IPC 提权

### 已知限制

- 本项目**未**经过专业安全审计,不应直接用于生产关键基础设施
- LLM 输出未做完整 prompt injection 防护,建议在关键路径增加人工审核
- 知识图谱 LLM 抽取的实体未做完整性校验,可能被注入恶意关系
- DNC 协议(MTConnect/OPC UA)默认配置可能未启用 TLS,需部署者自行加固

---

## Disclosure Policy

- 我们遵循 **Coordinated Disclosure**(协同披露)原则
- 在补丁发布前,**请勿**公开发布漏洞详情或 PoC
- 补丁发布后,我们将在 GitHub Security Advisory 公开致谢首位报告者(若报告者同意)
- 我们**不**对安全漏洞的研究提供金钱奖励,但会在发布说明中致谢

---

## Contact

- **安全报告**:[GitHub Security Advisory](https://github.com/printing10101/Virtual-Realm-Manufacturing/security/advisories/new)
- **一般问题**:[GitHub Issues](https://github.com/printing10101/Virtual-Realm-Manufacturing/issues)(仅非安全问题)
- **紧急**:若漏洞影响已部署的生产环境且需紧急响应,请在 Security Advisory 中标注 `[URGENT]`

---

## Acknowledgments

感谢以下为灵境制造安全做出贡献的研究者(按报告时间排序):

<!-- 安全漏洞报告者致谢列表,补丁发布后更新 -->
- _暂无_

---

**Last updated**: 2025
