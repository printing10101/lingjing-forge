"""人工审核界面（M1.4）。

提供基础版 Web 界面，用于审核 LLM 抽取的实体和关系数据：
    - 展示待审核的抽取结果
    - 支持实体/关系的确认、修改和删除操作
    - 实现审核状态跟踪（未审核/已审核/需修改）

用法::

    # 启动审核服务
    python -m app.knowledge_graph.extractor.review

    # 或在代码中使用
    from app.knowledge_graph.extractor.review import ReviewManager
    manager = ReviewManager()
    manager.load_extraction_result(result)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# 模板加载

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    """从 templates 目录读取指定模板文件内容。

    Args:
        name: 模板文件名（如 ``review.html``）。

    Returns:
        str: 模板文件的文本内容。
    """
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


# 数据模型


class ReviewStatus:
    """审核状态常量。"""

    UNVERIFIED = "unverified"  # 未审核
    APPROVED = "approved"  # 已审核
    NEEDS_REVISION = "needs_revision"  # 需修改


class EntityReview(BaseModel):
    """实体审核数据模型。"""

    id: str
    entity_type: str
    name: str
    properties: dict[str, Any] = {}
    confidence: float = 50.0
    status: str = ReviewStatus.UNVERIFIED
    review_comment: str = ""
    reviewed_at: str | None = None


class RelationReview(BaseModel):
    """关系审核数据模型。"""

    source_id: str
    target_id: str
    relation_type: str
    properties: dict[str, Any] = {}
    confidence: float = 50.0
    status: str = ReviewStatus.UNVERIFIED
    review_comment: str = ""
    reviewed_at: str | None = None


class ExtractionReviewData(BaseModel):
    """抽取结果审核数据。"""

    id: str
    source_path: str
    extraction_method: str = ""
    total_pages: int = 0
    processed_pages: int = 0
    entities: list[EntityReview] = []
    relations: list[RelationReview] = []
    validation_report: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
    overall_status: str = ReviewStatus.UNVERIFIED


# 审核管理器


@dataclass
class ReviewManager:
    """审核结果管理器。

    负责存储和管理待审核的抽取结果。
    """

    reviews: dict[str, ExtractionReviewData] = field(default_factory=dict)
    storage_path: Path = Path("./data/reviews")

    def __post_init__(self):
        """初始化存储目录。"""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        # 并发保护：ReviewManager 单例在多个 async 端点间共享，
        # reviews 字典的并发修改与 _save_to_disk 的文件写入均需加锁。
        # 使用 RLock 以允许 _save_to_disk 在已持锁的方法内部被调用时
        # 复用同一把锁，避免死锁。
        self._lock = threading.RLock()

    def load_extraction_result(
        self,
        result: dict[str, Any],
    ) -> str:
        """加载抽取结果到审核队列。

        Args:
            result: 抽取结果字典，包含 entities 和 relations。

        Returns:
            str: 审核记录 ID。
        """
        review_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        # 转换实体
        entities = []
        for ent in result.get("entities", []):
            entities.append(
                EntityReview(
                    id=ent.get("id", ""),
                    entity_type=ent.get("entity_type", ""),
                    name=ent.get("name", ""),
                    properties=ent.get("properties", {}),
                    confidence=float(ent.get("confidence", 50)),
                    status=ent.get("status", ReviewStatus.UNVERIFIED),
                )
            )

        # 转换关系
        relations = []
        for rel in result.get("relations", []):
            relations.append(
                RelationReview(
                    source_id=rel.get("source_id", ""),
                    target_id=rel.get("target_id", ""),
                    relation_type=rel.get("relation_type", ""),
                    properties=rel.get("properties", {}),
                    confidence=float(rel.get("confidence", 50)),
                    status=rel.get("status", ReviewStatus.UNVERIFIED),
                )
            )

        # 创建审核记录
        review = ExtractionReviewData(
            id=review_id,
            source_path=result.get("source_path", ""),
            extraction_method=result.get("extraction_method", ""),
            total_pages=result.get("total_pages", 0),
            processed_pages=result.get("processed_pages", 0),
            entities=entities,
            relations=relations,
            validation_report=result.get("validation_report"),
            created_at=now,
            updated_at=now,
            overall_status=ReviewStatus.UNVERIFIED,
        )

        with self._lock:
            self.reviews[review_id] = review
            self._save_to_disk(review)

        logger.info(
            "加载审核记录 %s: %d 个实体, %d 个关系",
            review_id,
            len(entities),
            len(relations),
        )

        return review_id

    def get_review(self, review_id: str) -> ExtractionReviewData | None:
        """获取审核记录。"""
        with self._lock:
            return self.reviews.get(review_id)

    def list_reviews(self) -> list[dict[str, Any]]:
        """列出所有审核记录摘要。"""
        with self._lock:
            result = []
            for review_id, review in self.reviews.items():
                approved_entities = sum(1 for e in review.entities if e.status == ReviewStatus.APPROVED)
                approved_relations = sum(1 for r in review.relations if r.status == ReviewStatus.APPROVED)
                result.append(
                    {
                        "id": review_id,
                        "source_path": review.source_path,
                        "total_entities": len(review.entities),
                        "approved_entities": approved_entities,
                        "total_relations": len(review.relations),
                        "approved_relations": approved_relations,
                        "overall_status": review.overall_status,
                        "created_at": review.created_at,
                    }
                )
            return result

    def update_entity(
        self,
        review_id: str,
        entity_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """更新实体审核状态。

        Args:
            review_id: 审核记录 ID。
            entity_id: 实体 ID。
            updates: 更新内容，可包含 status, review_comment 等。

        Returns:
            bool: 是否更新成功。
        """
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return False

            for entity in review.entities:
                if entity.id == entity_id:
                    if "status" in updates:
                        entity.status = updates["status"]
                        entity.reviewed_at = datetime.now(timezone.utc).isoformat()
                    if "review_comment" in updates:
                        entity.review_comment = updates["review_comment"]
                    if "name" in updates:
                        entity.name = updates["name"]
                    if "properties" in updates:
                        entity.properties = updates["properties"]

                    review.updated_at = datetime.now(timezone.utc).isoformat()
                    self._update_overall_status(review)
                    self._save_to_disk(review)
                    return True

            return False

    def update_relation(
        self,
        review_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        updates: dict[str, Any],
    ) -> bool:
        """更新关系审核状态。

        Args:
            review_id: 审核记录 ID。
            source_id: 源实体 ID。
            target_id: 目标实体 ID。
            relation_type: 关系类型。
            updates: 更新内容。

        Returns:
            bool: 是否更新成功。
        """
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return False

            for relation in review.relations:
                if (
                    relation.source_id == source_id
                    and relation.target_id == target_id
                    and relation.relation_type == relation_type
                ):
                    if "status" in updates:
                        relation.status = updates["status"]
                        relation.reviewed_at = datetime.now(timezone.utc).isoformat()
                    if "review_comment" in updates:
                        relation.review_comment = updates["review_comment"]
                    if "properties" in updates:
                        relation.properties = updates["properties"]

                    review.updated_at = datetime.now(timezone.utc).isoformat()
                    self._update_overall_status(review)
                    self._save_to_disk(review)
                    return True

            return False

    def delete_entity(self, review_id: str, entity_id: str) -> bool:
        """删除实体（标记为删除状态）。"""
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return False

            for i, entity in enumerate(review.entities):
                if entity.id == entity_id:
                    entity.status = "deleted"
                    entity.reviewed_at = datetime.now(timezone.utc).isoformat()
                    review.updated_at = datetime.now(timezone.utc).isoformat()
                    self._update_overall_status(review)
                    self._save_to_disk(review)
                    return True

            return False

    def delete_relation(
        self,
        review_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
    ) -> bool:
        """删除关系（标记为删除状态）。"""
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return False

            for relation in review.relations:
                if (
                    relation.source_id == source_id
                    and relation.target_id == target_id
                    and relation.relation_type == relation_type
                ):
                    relation.status = "deleted"
                    relation.reviewed_at = datetime.now(timezone.utc).isoformat()
                    review.updated_at = datetime.now(timezone.utc).isoformat()
                    self._update_overall_status(review)
                    self._save_to_disk(review)
                    return True

            return False

    def approve_all(self, review_id: str) -> bool:
        """批量批准所有实体和关系。"""
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return False

            now = datetime.now(timezone.utc).isoformat()
            for entity in review.entities:
                if entity.status == ReviewStatus.UNVERIFIED:
                    entity.status = ReviewStatus.APPROVED
                    entity.reviewed_at = now

            for relation in review.relations:
                if relation.status == ReviewStatus.UNVERIFIED:
                    relation.status = ReviewStatus.APPROVED
                    relation.reviewed_at = now

            review.overall_status = ReviewStatus.APPROVED
            review.updated_at = now
            self._save_to_disk(review)
            return True

    def get_approved_data(self, review_id: str) -> dict[str, Any] | None:
        """获取已批准的数据，可用于写入图谱。

        Args:
            review_id: 审核记录 ID。

        Returns:
            dict: 包含已批准实体和关系的字典。
        """
        with self._lock:
            review = self.reviews.get(review_id)
            if not review:
                return None

            entities = [
                {
                    "entity_type": e.entity_type,
                    "id": e.id,
                    "name": e.name,
                    "properties": e.properties,
                    "confidence": e.confidence,
                }
                for e in review.entities
                if e.status == ReviewStatus.APPROVED
            ]

            relations = [
                {
                    "relation_type": r.relation_type,
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "properties": r.properties,
                    "confidence": r.confidence,
                }
                for r in review.relations
                if r.status == ReviewStatus.APPROVED
            ]

            return {
                "entities": entities,
                "relations": relations,
                "source_path": review.source_path,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }

    def _update_overall_status(self, review: ExtractionReviewData) -> None:
        """更新整体审核状态。"""
        all_items = list(review.entities) + list(review.relations)
        if not all_items:
            review.overall_status = ReviewStatus.APPROVED
            return

        statuses = [item.status for item in all_items if item.status != "deleted"]
        if not statuses:
            review.overall_status = ReviewStatus.APPROVED
        elif all(s == ReviewStatus.APPROVED for s in statuses):
            review.overall_status = ReviewStatus.APPROVED
        elif any(s == ReviewStatus.NEEDS_REVISION for s in statuses):
            review.overall_status = ReviewStatus.NEEDS_REVISION
        else:
            review.overall_status = ReviewStatus.UNVERIFIED

    def _save_to_disk(self, review: ExtractionReviewData) -> None:
        """保存审核记录到磁盘。

        使用 RLock 以允许在已持锁的公开方法内部被调用时复用锁，
        同时也可单独被调用而不影响正确性。
        """
        with self._lock:
            file_path = self.storage_path / f"{review.id}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(review.model_dump(), f, ensure_ascii=False, indent=2)

    def load_from_disk(self) -> None:
        """从磁盘加载所有审核记录。"""
        with self._lock:
            if not self.storage_path.exists():
                return

            for file_path in self.storage_path.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    review = ExtractionReviewData(**data)
                    self.reviews[review.id] = review
                except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as exc:
                    logger.warning("加载审核记录失败 %s: %s", file_path, exc)


# FastAPI 路由


router = APIRouter(prefix="/review", tags=["review"])
review_manager = ReviewManager()


@router.on_event("startup")
async def startup_event():
    """启动时加载审核记录。"""
    review_manager.load_from_disk()


@router.get("/", response_class=HTMLResponse)
async def review_page():
    """审核界面首页。"""
    return REVIEW_HTML_TEMPLATE


@router.get("/api/reviews")
async def list_reviews():
    """列出所有审核记录。"""
    return {"reviews": review_manager.list_reviews()}


@router.get("/api/reviews/{review_id}")
async def get_review(review_id: str):
    """获取审核记录详情。"""
    review = review_manager.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="审核记录不存在")
    return review.model_dump()


@router.patch("/api/reviews/{review_id}/entities/{entity_id}")
async def update_entity(review_id: str, entity_id: str, request: Request):
    """更新实体。"""
    updates = await request.json()
    success = review_manager.update_entity(review_id, entity_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="实体不存在")
    return {"success": True}


@router.delete("/api/reviews/{review_id}/entities/{entity_id}")
async def delete_entity(review_id: str, entity_id: str):
    """删除实体。"""
    success = review_manager.delete_entity(review_id, entity_id)
    if not success:
        raise HTTPException(status_code=404, detail="实体不存在")
    return {"success": True}


@router.patch("/api/reviews/{review_id}/relations")
async def update_relation(review_id: str, request: Request):
    """更新关系。"""
    data = await request.json()
    success = review_manager.update_relation(
        review_id,
        data["source_id"],
        data["target_id"],
        data["relation_type"],
        data.get("updates", {}),
    )
    if not success:
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"success": True}


@router.delete("/api/reviews/{review_id}/relations")
async def delete_relation(review_id: str, request: Request):
    """删除关系。"""
    data = await request.json()
    success = review_manager.delete_relation(
        review_id,
        data["source_id"],
        data["target_id"],
        data["relation_type"],
    )
    if not success:
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"success": True}


@router.post("/api/reviews/{review_id}/approve-all")
async def approve_all(review_id: str):
    """批量批准所有项目。"""
    success = review_manager.approve_all(review_id)
    if not success:
        raise HTTPException(status_code=404, detail="审核记录不存在")
    return {"success": True}


@router.get("/api/reviews/{review_id}/approved")
async def get_approved_data(review_id: str):
    """获取已批准的数据。"""
    data = review_manager.get_approved_data(review_id)
    if data is None:
        raise HTTPException(status_code=404, detail="审核记录不存在")
    return data


@router.post("/api/load")
async def load_extraction_result(request: Request):
    """加载抽取结果到审核队列。"""
    data = await request.json()
    review_id = review_manager.load_extraction_result(data)
    return {"review_id": review_id}


# HTML 模板


REVIEW_HTML_TEMPLATE = _load_template("review.html")


# 独立运行


def create_review_app() -> FastAPI:
    """创建审核应用实例。"""
    app = FastAPI(title="知识图谱抽取结果审核", version="1.0.0")
    app.include_router(router)
    return app


def main():  # pragma: no cover
    """独立运行审核服务。"""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_review_app()
    # 默认仅监听本机回环，避免暴露到外网；如需远程访问可通过 env 显式指定绑定地址。
    host = os.environ.get("KG_REVIEW_HOST", "127.0.0.1")
    port = int(os.environ.get("KG_REVIEW_PORT", "8001"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
