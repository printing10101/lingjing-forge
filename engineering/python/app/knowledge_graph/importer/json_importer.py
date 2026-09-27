"""知识图谱 JSON 导入 re-export shim（M1.3 重构 P1-4）

向后兼容
--------
本模块已拆分为：
    - :mod:`app.knowledge_graph.importer.importers._common`
        共享基础设施（常量 / 数据类 / 去重器 / 辅助函数 / 路径定义）。
    - :mod:`app.knowledge_graph.importer.importers.material_importer`
        ``import_materials`` 函数。
    - :mod:`app.knowledge_graph.importer.importers.tool_importer`
        ``import_tools`` 函数。
    - :mod:`app.knowledge_graph.importer.importers.machine_importer`
        ``import_machines`` 函数。
    - :mod:`app.knowledge_graph.importer.importers.process_importer`
        ``import_process_rules`` 函数。
    - :mod:`app.knowledge_graph.importer.coordinator`
        协调器（``import_all`` / ``load_graph_from_repository`` / ``main``）。

本文件仅 re-export 上述模块的全部公开符号，保证历史导入语句
``from app.knowledge_graph.importer.json_importer import ...`` 继续可用，
包含测试用到的内部符号（``_MaterialDeduper`` / ``_slugify_id`` 等）与
路径常量（``MATERIALS_JSON`` 等）。

注意：路径常量 ``MATERIALS_JSON`` / ``TOOLS_JSON`` / ``MACHINES_JSON``
/ ``PROCESS_RULES_JSON`` 在本模块上以属性形式 re-export；测试通过
``monkeypatch.setattr(json_importer, "MATERIALS_JSON", ...)`` 替换后，
各导入器会通过 :func:`_common._resolve_default_path` 在调用时读取本
模块上的最新值。
"""

from __future__ import annotations

# 共享基础设施：常量 / 数据类 / 去重器 / 辅助函数 / 路径定义
from app.knowledge_graph.importer.importers._common import (
    # 路径常量
    MATERIALS_JSON,
    TOOLS_JSON,
    MACHINES_JSON,
    PROCESS_RULES_JSON,
    # 节点 / 关系类型常量
    EDGE_APPLIED_TO,
    EDGE_SUITABLE_FOR,
    EDGE_USED,
    NODE_TYPE_FEATURE,
    NODE_TYPE_MACHINE,
    NODE_TYPE_MATERIAL,
    NODE_TYPE_PROCESS,
    NODE_TYPE_TOOL,
    # 映射常量
    ImportReport,
    ImportStats,
    # logger（历史代码可能依赖 json_importer.logger）
)

# 4 个导入器函数
from app.knowledge_graph.importer.importers.machine_importer import (
    import_machines,
)
from app.knowledge_graph.importer.importers.material_importer import (
    import_materials,
)
from app.knowledge_graph.importer.importers.process_importer import (
    import_process_rules,
)
from app.knowledge_graph.importer.importers.tool_importer import (
    import_tools,
)

# 协调器：import_all / load_graph_from_repository / main
from app.knowledge_graph.importer.coordinator import (
    import_all,
    load_graph_from_repository,
    main,
)

# 为兼容 ``from app.knowledge_graph.importer.json_importer import logger``
import logging

logger = logging.getLogger(__name__)

__all__ = [
    # 主入口
    "import_all",
    "import_materials",
    "import_tools",
    "import_machines",
    "import_process_rules",
    "load_graph_from_repository",
    "main",
    # 数据类
    "ImportReport",
    "ImportStats",
    # 路径常量
    "MATERIALS_JSON",
    "TOOLS_JSON",
    "MACHINES_JSON",
    "PROCESS_RULES_JSON",
    # 类型常量
    "NODE_TYPE_MATERIAL",
    "NODE_TYPE_TOOL",
    "NODE_TYPE_MACHINE",
    "NODE_TYPE_FEATURE",
    "NODE_TYPE_PROCESS",
    "EDGE_SUITABLE_FOR",
    "EDGE_APPLIED_TO",
    "EDGE_USED",
]
