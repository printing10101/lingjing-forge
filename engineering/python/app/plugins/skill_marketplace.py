from __future__ import annotations

import json
import logging
import os
import re
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.plugins.skill_loader import (
    SkillLevel,
    get_skill_loader,
)

logger = logging.getLogger(__name__)

DEFAULT_MARKET_DIR = ".lingjing/skills/.marketplace"

# 安全加固（2026-09）：skill_id / version 会拼进 .skz 包文件路径，必须过
# 白名单正则——字母/数字/下划线开头，仅含 [\w.-]，杜绝 ``..`` 与路径分隔符
# 等穿越载荷经 API 进入文件名。
_SAFE_NAME_RE = re.compile(r"^[\w][\w.\-]{0,127}$")


def _ensure_safe_skill_id(skill_id: str) -> None:
    if not _SAFE_NAME_RE.match(skill_id or ""):
        raise ValueError(f"非法 skill_id（仅允许字母数字与 ._- ，且不得包含路径分隔符）：{skill_id!r}")


@dataclass
class MarketListing:
    skill_id: str
    name: str
    version: str
    description: str
    author: str
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    avg_rating: float = 0.0
    rating_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class SkillMarketplace:
    def __init__(self, market_dir: str | None = None):
        self.market_dir = market_dir or DEFAULT_MARKET_DIR
        os.makedirs(self.market_dir, exist_ok=True)
        self._listings_file = os.path.join(self.market_dir, "listings.json")
        # 修复 [并发安全]：保护 ``_listings`` 内存视图与 listings.json 文件写，
        # 避免多线程下 publish/download/rate/unpublish 之间的覆盖与丢更新。
        self._lock = threading.Lock()
        self._listings: dict[str, MarketListing] = self._load_listings()

    def _load_listings(self) -> dict[str, MarketListing]:
        if not os.path.exists(self._listings_file):
            return {}
        try:
            with open(self._listings_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {k: MarketListing(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to load marketplace listings: %s", e)
            return {}

    def _save_listings(self) -> None:
        # 修复 [并发安全]：写 listings.json 走临时文件 + 原子替换，避免
        # ``publish`` 和 ``rate_skill`` 同时写文件时产生半写状态。
        data = {
            k: {
                "skill_id": v.skill_id,
                "name": v.name,
                "version": v.version,
                "description": v.description,
                "author": v.author,
                "tags": v.tags,
                "downloads": v.downloads,
                "avg_rating": v.avg_rating,
                "rating_count": v.rating_count,
                "created_at": v.created_at,
                "updated_at": v.updated_at,
            }
            for k, v in self._listings.items()
        }
        # 修复 [并发安全]：先写临时文件再原子替换，避免半写状态被并发读
        # 进程加载到内存导致 listings 视图抖动。
        tmp_file = f"{self._listings_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, self._listings_file)

    def publish(
        self,
        skill_id: str,
        author: str,
        level: SkillLevel = SkillLevel.PROJECT,
        sub_id: str | None = None,
    ) -> dict[str, Any] | None:
        _ensure_safe_skill_id(skill_id)
        loader = get_skill_loader()
        package = loader.export_skill(skill_id)
        if package is None:
            logger.warning("Skill not found for publish: %s", skill_id)
            return None

        meta = package.get("metadata", {})
        now = datetime.now(timezone.utc).isoformat()

        listing = MarketListing(
            skill_id=skill_id,
            name=meta.get("name", skill_id),
            version=meta.get("version", "1.0.0"),
            description=meta.get("description", ""),
            author=author,
            tags=meta.get("tags", []),
            created_at=now,
            updated_at=now,
        )

        # 修复 [并发安全]：持锁保护 _listings 修改 + 落盘。
        with self._lock:
            self._listings[skill_id] = listing

            pkg_file = self._build_package(package)
            self._save_listings()

        logger.info("Skill published: %s by %s", skill_id, author)
        return {
            "skill_id": skill_id,
            "listing": listing,
            "package_path": pkg_file,
        }

    def _build_package(self, package: dict[str, Any]) -> str:
        skill_id = package["skill_id"]
        _ensure_safe_skill_id(skill_id)
        version = str(package.get("metadata", {}).get("version", "1.0.0"))
        if not _SAFE_NAME_RE.match(version):
            # version 来自技能元数据（半可信输入），不合法时宁可拒绝也不落盘
            raise ValueError(f"非法技能版本号，拒绝打包：{version!r}")
        package_file = os.path.join(self.market_dir, f"{skill_id}_{version}.skz")

        with zipfile.ZipFile(package_file, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "skill_id": skill_id,
                        "name": package.get("name", ""),
                        "version": package.get("version", "1.0.0"),
                        "metadata": package.get("metadata", {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            zf.writestr("skill.md", package.get("raw_content", ""))

        logger.info("Package built: %s", package_file)
        return package_file

    def list_available(self, tag: str | None = None) -> list[dict[str, Any]]:
        # 修复 [并发安全]：持锁遍历 _listings，避免其他线程在迭代过程中增删
        # 键引发 RuntimeError: dictionary changed size during iteration。
        with self._lock:
            snapshot = list(self._listings.items())
        results = []
        for skill_id, listing in snapshot:
            if tag and tag not in listing.tags:
                continue
            results.append(
                {
                    "skill_id": listing.skill_id,
                    "name": listing.name,
                    "version": listing.version,
                    "description": listing.description,
                    "author": listing.author,
                    "tags": listing.tags,
                    "downloads": listing.downloads,
                    "avg_rating": listing.avg_rating,
                    "rating_count": listing.rating_count,
                    "created_at": listing.created_at,
                    "updated_at": listing.updated_at,
                }
            )

        results.sort(key=lambda x: (x["avg_rating"], x["downloads"]), reverse=True)
        return results

    def search(self, query: str) -> list[dict[str, Any]]:
        # 修复 [并发安全]：持锁读取快照后再做匹配。
        with self._lock:
            snapshot = list(self._listings.items())
        query_lower = query.lower()
        results = []
        for skill_id, listing in snapshot:
            searchable = f"{listing.name} {listing.description} {' '.join(listing.tags)} {listing.author}"
            if query_lower in searchable.lower():
                results.append(
                    {
                        "skill_id": listing.skill_id,
                        "name": listing.name,
                        "version": listing.version,
                        "description": listing.description,
                        "author": listing.author,
                        "tags": listing.tags,
                        "downloads": listing.downloads,
                        "avg_rating": listing.avg_rating,
                        "rating_count": listing.rating_count,
                    }
                )

        results.sort(key=lambda x: (x["avg_rating"], x["downloads"]), reverse=True)
        return results

    def download(
        self,
        skill_id: str,
        target_level: SkillLevel = SkillLevel.PROJECT,
        target_sub_id: str | None = None,
    ) -> dict[str, Any] | None:
        _ensure_safe_skill_id(skill_id)
        loader = get_skill_loader()

        matching_files = [f for f in os.listdir(self.market_dir) if f.startswith(f"{skill_id}_") and f.endswith(".skz")]
        if not matching_files:
            logger.warning("Package not found for download: %s", skill_id)
            return None

        package_path = os.path.join(self.market_dir, matching_files[0])

        with zipfile.ZipFile(package_path, "r") as zf:
            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)
            skill_md = zf.read("skill.md").decode("utf-8")

        imported = loader.import_skill(
            {
                "skill_id": skill_id,
                "raw_content": skill_md,
                "metadata": manifest.get("metadata", {}),
            },
            level=target_level,
            sub_id=target_sub_id,
        )

        if skill_id in self._listings:
            # 修复 [并发安全]：持锁修改计数 + 落盘，避免与其他 download/rate 调用
            # 产生覆盖。
            with self._lock:
                self._listings[skill_id].downloads += 1
                self._save_listings()

        logger.info("Skill downloaded: %s", skill_id)
        return {
            "skill_id": skill_id,
            "imported": imported is not None,
            "level": target_level.value,
            "sub_id": target_sub_id,
        }

    def rate_skill(self, skill_id: str, rating: float, agent_id: str = "") -> dict[str, Any]:
        loader = get_skill_loader()
        result = loader.rate_skill(skill_id, rating)

        if skill_id in self._listings:
            # 修复 [并发安全]：持锁修改评分字段 + 落盘。
            with self._lock:
                self._listings[skill_id].avg_rating = result["avg_rating"]
                self._listings[skill_id].rating_count = result["rating_count"]
                self._save_listings()

        return {
            **result,
            "agent_id": agent_id,
        }

    def unpublish(self, skill_id: str) -> bool:
        _ensure_safe_skill_id(skill_id)
        # 修复 [并发安全]：持锁删除 + 落盘 + 文件清理。
        with self._lock:
            if skill_id not in self._listings:
                return False
            del self._listings[skill_id]
            self._save_listings()
            matching_files = [
                f for f in os.listdir(self.market_dir) if f.startswith(f"{skill_id}_") and f.endswith(".skz")
            ]
            for f in matching_files:
                try:
                    os.remove(os.path.join(self.market_dir, f))
                except OSError as exc:
                    logger.warning("Failed to remove package %s: %s", f, exc)

        logger.info("Skill unpublished: %s", skill_id)
        return True

    def get_stats(self) -> dict[str, Any]:
        # 修复 [并发安全]：持锁访问 _listings 的可变视图，避免并发迭代出错。
        with self._lock:
            listings_view = self._listings.values()
            total = len(self._listings)
            most_downloaded = max(listings_view, key=lambda x: x.downloads, default=None)
            highest_rated = max(listings_view, key=lambda x: x.avg_rating, default=None)
        return {
            "total_listings": total,
            "market_dir": self.market_dir,
            "most_downloaded": most_downloaded,
            "highest_rated": highest_rated,
        }


class _MarketplaceHolder:
    """Thread-safe lazy holder for the :class:`SkillMarketplace` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: SkillMarketplace | None = None

    def get(self, market_dir: str | None = None) -> SkillMarketplace:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = SkillMarketplace(market_dir)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _MarketplaceHolder()


def get_marketplace(market_dir: str | None = None) -> SkillMarketplace:
    """获取共享的 :class:`SkillMarketplace` 单例；首次访问时懒初始化。

    Returns:
        :class:`SkillMarketplace` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_marketplace)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get(market_dir)
