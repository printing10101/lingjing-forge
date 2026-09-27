"""知识图谱 JSON 导入器子包（M1.3 重构 P1-4）。

子模块
------
- :mod:`._common`           共享基础设施（常量 / 数据类 / 去重器 / 辅助函数）
- :mod:`.material_importer` ``materials.json`` → ``material`` 节点
- :mod:`.tool_importer`     ``tools.json`` → ``tool`` 节点 + 关系
- :mod:`.machine_importer`  ``machines.json`` → ``machine`` 节点
- :mod:`.process_importer`  ``process_rules.json`` → ``process`` 节点 + 关系

协调逻辑位于 :mod:`app.knowledge_graph.importer.coordinator`。
"""

from app.knowledge_graph.importer.importers._common import (
    EDGE_APPLIED_TO,
    EDGE_SUITABLE_FOR,
    EDGE_USED,
    NODE_TYPE_FEATURE,
    NODE_TYPE_MACHINE,
    NODE_TYPE_MATERIAL,
    NODE_TYPE_PROCESS,
    NODE_TYPE_TOOL,
    ImportReport,
    ImportStats,
    _MachineDeduper,
    _MaterialDeduper,
    _ToolDeduper,
    _FEATURE_TO_REPRESENTATIVE_TOOLS,
    _SERIES_TO_FEATURES,
    _ALL_MATERIAL_NAMES,
    _load_json,
    _material_id_from_name,
    _retry_with_backoff,
    _slugify_id,
)
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

__all__ = [
    "import_materials",
    "import_tools",
    "import_machines",
    "import_process_rules",
    "ImportStats",
    "ImportReport",
]
