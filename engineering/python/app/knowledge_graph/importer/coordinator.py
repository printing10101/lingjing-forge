"""知识图谱 JSON 导入协调器（M1.3 重构 P1-4）

职责
----
- 协调 4 个独立 ``import_<file>`` 函数，按顺序导入 4 个冷启动 JSON 数据源。
- 可选地把内存图落库到 PostgreSQL（事务原子性）。
- 收集导入统计信息（成功 / 重复 / 失败 / 各类型数量），产出
  :class:`ImportReport`。

顺序说明：先 materials → tools → machines → process_rules。前三者
建立基础节点，最后 process_rules 引用前述节点生成关系。
"""

from __future__ import annotations

import logging
import time


from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.importer.importers._common import ImportReport
from app.knowledge_graph.importer.importers.machine_importer import (
    import_machines,
)
from app.knowledge_graph.importer.importers.material_importer import (
    import_materials,
)
from app.knowledge_graph.importer.importers.process_importer import (
    import_process_rules,
)
from app.knowledge_graph.importer.importers.tool_importer import import_tools

logger = logging.getLogger(__name__)


def import_all(
    graph: GraphStore | None = None,
    *,
    flush_to_db: bool = True,
    db_clear_first: bool = False,
) -> ImportReport:
    """导入全部 4 个 JSON 文件，并可选落库到 PostgreSQL。

    顺序说明：先 materials → tools → machines → process_rules。前三者
    建立基础节点，最后 process_rules 引用前述节点生成关系。

    Args:
        graph: 可选外部传入 :class:`GraphStore`；若为 ``None`` 则内部新建。
        flush_to_db: 是否在导入完成后将内存图落库。
        db_clear_first: 落库前是否先清空 kg_nodes / kg_edges。

    Returns:
        :class:`ImportReport`
    """
    if graph is None:
        graph = GraphStore()

    report = ImportReport()
    report.started_at = time.time()

    logger.info("Starting import_all: materials -> tools -> machines -> process_rules")

    # 阶段 1：导入材料
    report.materials = import_materials(graph)

    # 阶段 2：导入刀具
    report.tools = import_tools(graph)

    # 阶段 3：导入机床
    report.machines = import_machines(graph)

    # 阶段 4：导入工艺规则（依赖前述节点）
    report.process_rules = import_process_rules(graph)

    # 汇总
    report.total_nodes = graph.node_count()
    report.total_edges = graph.edge_count()
    report.finished_at = time.time()

    # 落库（可选）
    db_message = ""
    if flush_to_db:
        try:
            stats = graph.flush_to_repository(clear_first=db_clear_first)
            db_message = f"flushed to DB: nodes={stats.get('nodes_written', 0)}, edges={stats.get('edges_written', 0)}"
            logger.info(db_message)
        except (OSError, RuntimeError) as exc:
            db_message = f"flush_to_repository skipped/failed: {exc}"
            logger.warning(db_message)

    total_failed = report.materials.failed + report.tools.failed + report.machines.failed + report.process_rules.failed
    report.overall_success = total_failed == 0
    report.overall_message = (
        f"导入完成：{report.total_nodes} 节点 {report.total_edges} 关系。"
        f"失败 {total_failed} 条。" + (db_message if db_message else "")
    )

    # 控制台输出固定格式
    logger.info("导入完成：%s 节点 %s 关系", report.total_nodes, report.total_edges)
    if total_failed > 0:
        logger.warning("  警告：%s 条记录失败，详情见 report", total_failed)

    return report


def load_graph_from_repository(*, replace: bool = True) -> GraphStore:
    """从 PostgreSQL 加载已有图数据到新的 :class:`GraphStore` 实例。"""
    g = GraphStore(auto_load=False)
    try:
        g.load_from_repository(replace=replace)
    except (OSError, RuntimeError) as exc:
        logger.warning("load_graph_from_repository failed: %s", exc)
    return g


def main() -> int:
    """CLI 入口：执行全量导入并打印简要结果。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    report = import_all(flush_to_db=True, db_clear_first=False)
    logger.info("")
    logger.info(report.render_markdown())
    return 0 if report.overall_success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
