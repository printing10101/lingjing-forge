"""共享 SQLAlchemy declarative Base（从 training_task 拆出）。

供 training_task / _rbac_models 及其余模型模块共用同一 metadata，
确保 init_db 的 create_all 覆盖全部表。
"""

from __future__ import annotations

from sqlalchemy.orm import declarative_base


Base = declarative_base()
