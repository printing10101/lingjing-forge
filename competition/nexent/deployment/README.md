# 本机搜索恢复配置

本次验证只适用于 D:/nexent-deploy 的本机试运行。Elasticsearch 使用独立恢复目录
`D:/nexent-deploy/nexent-files/elasticsearch-recovered-i9`，原 C 盘数据及完整备份仍保留。
覆盖文件将 JVM 堆调整为 512 MB；这是本机资源预算，不是生产容量建议。

后续重新创建服务时须同时包含 Nexent 原 compose 文件和 `compose.lingjing-search.yml`，
设置 `LINGJING_ES_DATA_DIR` 为上述已验证目录，并保留原 compose 项目名 `nexent`。
需要加载私有 `nexent/deploy/env/.env` 以及 `image-source.mainland.env` 的镜像变量，
保留当前 ES 镜像 `elastic.m.daocloud.io/elasticsearch/elasticsearch:8.17.4`。
只使用原 compose 文件会回到旧数据挂载，不能照此判定已恢复。
不要重新运行 I9 的首次复制/激活脚本；这些是一次性操作记录。

本机 9210 端口存在其他服务占用，搜索验收通过 Docker 内部服务名
`nexent-elasticsearch:9200` 完成，不能只凭 localhost:9210 的 HTTP 200 判定搜索可用。
私有登录文件、服务令牌、原始容器配置与备份均不得提交到仓库。

I10 新增 `compose.lingjing-worker.yml`，按本机预算将数据处理器、Ray actor 和 Celery 并发均设为 1，
对象存储预算 0.10 GB，关闭 Ray Dashboard 与 Flower。Ray 内存保护继续启用。
复用部署时也应包含这个覆盖文件；只降低并发仍不能证明 6 GB WSL 足以完成入库，详见 I10 验收报告。
本机在备份 `.wslconfig` 后将 WSL 内存上限改为 8 GB，重启后小文件入库通过。
MCP 启动应在仓库根目录使用 `python -m mcp_server.server`，避免直接运行文件时无法导入自身包。
