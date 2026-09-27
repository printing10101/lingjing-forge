"""性能基准数据仓库层。

提供对 benchmark_results.db 的 CRUD 操作，支持历史数据查询、
版本间对比和趋势分析。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import Session, selectinload

from tests.benchmarks.database.models import Base, BenchmarkResult, BenchmarkRun, init_db


class BenchmarkRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_db()
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)

    def _ensure_db(self) -> None:
        if not os.path.exists(self.db_path):
            init_db(self.db_path)

    def _get_git_info(self) -> dict[str, str | None]:
        try:
            commit_hash = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            tag = subprocess.run(
                ["git", "describe", "--tags", "--exact-match", "--always"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return {
                "commit_hash": commit_hash or None,
                "branch": branch or None,
                "tag": tag if tag and tag != commit_hash else None,
            }
        except Exception:
            return {"commit_hash": None, "branch": None, "tag": None}

    def create_run(
        self,
        results: dict[str, dict[str, Any]],
        summary: str = "",
        version: str | None = None,
    ) -> str:
        run_id = f"bench_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000000) % 1000000:06d}"
        git_info = self._get_git_info()

        total = 0
        passed = 0
        failed = 0
        has_regression = 0
        has_critical = 0

        with Session(self.engine) as session:
            run = BenchmarkRun(
                run_id=run_id,
                git_commit_hash=git_info["commit_hash"],
                git_branch=git_info["branch"],
                git_tag=git_info["tag"],
                version=version or git_info["tag"] or "",
                ci_run_id=os.environ.get("CI_RUN_ID"),
                ci_run_number=os.environ.get("CI_RUN_NUMBER"),
                runner_os=os.environ.get("RUNNER_OS"),
                python_version=os.environ.get("PYTHON_VERSION"),
                node_version=os.environ.get("NODE_VERSION"),
                summary=summary,
            )

            for bench_type, metrics in results.items():
                for metric_name, metric_data in metrics.items():
                    if isinstance(metric_data, dict):
                        value = metric_data.get("value", 0)
                        unit = metric_data.get("unit", "")
                        status = metric_data.get("status", "PASS")
                        extra = {k: v for k, v in metric_data.items() if k not in ("value", "unit", "status")}
                    else:
                        value = float(metric_data)
                        unit = ""
                        status = "PASS"
                        extra = {}

                    total += 1
                    if status in ("PASS", "IMPROVED"):
                        passed += 1
                    else:
                        failed += 1
                        if status == "CRITICAL":
                            has_critical = 1
                            has_regression = 1
                        elif status in ("WARNING", "VIOLATED"):
                            has_regression = 1

                    result = BenchmarkResult(
                        run_id=run_id,
                        benchmark_type=bench_type,
                        metric_name=metric_name,
                        metric_value=value,
                        metric_unit=unit,
                        status=status,
                        extra_data=json.dumps(extra) if extra else None,
                    )
                    session.add(result)

            run.total_benchmarks = total
            run.passed_benchmarks = passed
            run.failed_benchmarks = failed
            run.has_regression = has_regression
            run.has_critical = has_critical

            session.add(run)
            session.commit()

        return run_id

    def get_latest_run(self) -> BenchmarkRun | None:
        with Session(self.engine) as session:
            run = (
                session.query(BenchmarkRun)
                .options(selectinload(BenchmarkRun.results))
                .order_by(desc(BenchmarkRun.created_at))
                .first()
            )
            if run:
                session.expunge_all()
                return run
            return None

    def get_run_by_id(self, run_id: str) -> BenchmarkRun | None:
        with Session(self.engine) as session:
            return session.query(BenchmarkRun).filter(BenchmarkRun.run_id == run_id).first()

    def get_runs(
        self,
        limit: int = 20,
        offset: int = 0,
        branch: str | None = None,
    ) -> list[BenchmarkRun]:
        with Session(self.engine) as session:
            query = session.query(BenchmarkRun).order_by(desc(BenchmarkRun.created_at))
            if branch:
                query = query.filter(BenchmarkRun.git_branch == branch)
            return query.offset(offset).limit(limit).all()

    def get_metric_history(
        self,
        metric_name: str,
        limit: int = 50,
        branch: str | None = None,
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            query = (
                session.query(BenchmarkResult, BenchmarkRun)
                .join(BenchmarkRun, BenchmarkResult.run_id == BenchmarkRun.run_id)
                .filter(BenchmarkResult.metric_name == metric_name)
                .order_by(desc(BenchmarkRun.created_at))
            )
            if branch:
                query = query.filter(BenchmarkRun.git_branch == branch)

            results = []
            for result, run in query.limit(limit).all():
                results.append({
                    "run_id": run.run_id,
                    "git_commit_hash": run.git_commit_hash,
                    "git_branch": run.git_branch,
                    "version": run.version,
                    "created_at": run.created_at.isoformat() if run.created_at else "",
                    "metric_value": result.metric_value,
                    "metric_unit": result.metric_unit,
                    "status": result.status,
                })
            return results

    def compare_versions(
        self,
        run_id_a: str,
        run_id_b: str,
    ) -> dict[str, Any]:
        run_a = self.get_run_by_id(run_id_a)
        run_b = self.get_run_by_id(run_id_b)

        if not run_a or not run_b:
            return {"error": "One or both run IDs not found"}

        results_a = {(r.benchmark_type, r.metric_name): r for r in run_a.results}
        results_b = {(r.benchmark_type, r.metric_name): r for r in run_b.results}

        comparisons = []
        all_keys = set(results_a.keys()) | set(results_b.keys())

        for key in sorted(all_keys):
            ra = results_a.get(key)
            rb = results_b.get(key)
            bench_type, metric_name = key

            entry: dict[str, Any] = {
                "benchmark_type": bench_type,
                "metric_name": metric_name,
                "value_a": ra.metric_value if ra else None,
                "value_b": rb.metric_value if rb else None,
                "unit": ra.metric_unit if ra else (rb.metric_unit if rb else ""),
            }

            if ra and rb and ra.metric_value != 0:
                change_pct = (rb.metric_value - ra.metric_value) / ra.metric_value * 100
                entry["change_pct"] = round(change_pct, 2)
                entry["regression"] = abs(change_pct) > 20
            else:
                entry["change_pct"] = None
                entry["regression"] = False

            comparisons.append(entry)

        return {
            "run_a": {"run_id": run_a.run_id, "created_at": run_a.created_at.isoformat() if run_a.created_at else "", "git_commit": run_a.git_commit_hash},
            "run_b": {"run_id": run_b.run_id, "created_at": run_b.created_at.isoformat() if run_b.created_at else "", "git_commit": run_b.git_commit_hash},
            "comparisons": comparisons,
        }

    def get_summary_stats(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            total_runs = session.query(func.count(BenchmarkRun.id)).scalar() or 0
            total_results = session.query(func.count(BenchmarkResult.id)).scalar() or 0
            regression_runs = session.query(func.count(BenchmarkRun.id)).filter(
                BenchmarkRun.has_regression == 1,
            ).scalar() or 0

            latest = session.query(BenchmarkRun).order_by(desc(BenchmarkRun.created_at)).first()

            return {
                "total_runs": total_runs,
                "total_results": total_results,
                "regression_runs": regression_runs,
                "latest_run": latest.to_dict() if latest else None,
            }

    def get_benchmark_types(self) -> list[str]:
        with Session(self.engine) as session:
            types = session.query(BenchmarkResult.benchmark_type).distinct().all()
            return sorted([t[0] for t in types])

    def close(self) -> None:
        self.engine.dispose()
