"""兼容层模块。

由于 ``repository/`` 包目录会遮蔽此文件，实际实现已迁移至
``repository/json_repository.py``。本文件保留仅为向后兼容，
新代码应直接从 ``repository`` 包导入。
"""

from app.database.repository.json_repository import JsonRepository

__all__ = ["JsonRepository"]
