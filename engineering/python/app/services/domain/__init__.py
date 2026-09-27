"""领域服务层：CRUD 业务逻辑，位于 API 层和数据访问层之间。

服务执行数据库操作并返回纯数据结构（dict/list），不返回 HTTP 响应对象。

迁移自 ``app.service/``（单数），统一纳入 ``app.services.domain/``。
原 ``app.service.xxx`` 导入路径通过 ``app.service.__init__.py`` 的兼容 shim 保持可用。
"""

from app.services.domain import materials_service
from app.services.domain import equipment_service
from app.services.domain import process_routes_service
from app.services.domain import tools_service
from app.services.domain import quality_service
from app.services.domain import documents_service
from app.services.domain import production_service
