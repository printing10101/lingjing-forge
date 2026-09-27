"""工程文件管理系统。

提供基于ZIP的结构化工程文件打包/解析功能。
支持工程文件的创建、打开、保存、另存为操作。
"""

from __future__ import annotations

from app.projects.project_store import (
    ProjectStore,
    ProjectMetadata,
    ProjectManifest,
    ResourceEntry,
    PROJECT_FORMAT_VERSION,
    PROJECT_FILE_EXTENSION,
)
from app.projects.project_api import router as project_router

__all__ = [
    "ProjectStore",
    "ProjectMetadata",
    "ProjectManifest",
    "ResourceEntry",
    "PROJECT_FORMAT_VERSION",
    "PROJECT_FILE_EXTENSION",
    "project_router",
]
