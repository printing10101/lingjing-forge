"""性能基准数据库模型。

使用 SQLAlchemy ORM 定义性能基准测试结果的数据存储结构。
支持存储各版本的 LNN 推理、G代码生成、3D渲染等性能指标，
便于历史数据查询和版本间对比分析。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    git_commit_hash = Column(String(40), nullable=True)
    git_branch = Column(String(128), nullable=True)
    git_tag = Column(String(128), nullable=True)
    version = Column(String(32), nullable=True)
    ci_run_id = Column(String(64), nullable=True)
    ci_run_number = Column(String(16), nullable=True)
    runner_os = Column(String(32), nullable=True)
    python_version = Column(String(16), nullable=True)
    node_version = Column(String(16), nullable=True)
    total_benchmarks = Column(Integer, default=0)
    passed_benchmarks = Column(Integer, default=0)
    failed_benchmarks = Column(Integer, default=0)
    has_regression = Column(Integer, default=0)
    has_critical = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    results = relationship("BenchmarkResult", back_populates="run", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "git_commit_hash": self.git_commit_hash,
            "git_branch": self.git_branch,
            "git_tag": self.git_tag,
            "version": self.version,
            "ci_run_id": self.ci_run_id,
            "ci_run_number": self.ci_run_number,
            "runner_os": self.runner_os,
            "python_version": self.python_version,
            "node_version": self.node_version,
            "total_benchmarks": self.total_benchmarks,
            "passed_benchmarks": self.passed_benchmarks,
            "failed_benchmarks": self.failed_benchmarks,
            "has_regression": bool(self.has_regression),
            "has_critical": bool(self.has_critical),
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "results": [r.to_dict() for r in self.results],
        }


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), ForeignKey("benchmark_runs.run_id"), nullable=False, index=True)
    benchmark_type = Column(String(32), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False)
    metric_value = Column(Float, nullable=False)
    metric_unit = Column(String(32), nullable=True)
    status = Column(String(16), default="PASS")
    extra_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    run = relationship("BenchmarkRun", back_populates="results")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "benchmark_type": self.benchmark_type,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "metric_unit": self.metric_unit,
            "status": self.status,
            "extra_data": json.loads(self.extra_data) if self.extra_data else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db(db_path: str) -> None:
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()
