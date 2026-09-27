"""Session 数据源提取 mixin（从 session_extractor 拆出）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.dreaming._session_models import ProjectSession

logger = logging.getLogger(__name__)


class _SourcesMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    audit_log_dir: Any
    cam_reports_dir: Any
    cutting_store: Any
    mlflow_tracking_uri: Any

    def _extract_mlflow_sessions(self, cutoff: datetime) -> list[ProjectSession]:
        """从 MLflow 拉取实验 run，归一化为 ProjectSession。"""
        try:
            # P0#3 解耦: 通过 research_bridge 检查 MLflow 可用性
            from app.ai.lnn._research_bridge import get_has_mlflow

            HAS_MLFLOW = get_has_mlflow()
        except ImportError:
            HAS_MLFLOW = False

        if not HAS_MLFLOW:
            logger.debug("MLflow 未安装，跳过 MLflow session 提取")
            return []

        import mlflow

        mlflow.set_tracking_uri(self.mlflow_tracking_uri)

        # 搜索指定时间后的所有 run
        experiments = mlflow.search_experiments()
        sessions: list[ProjectSession] = []

        for exp in experiments:
            runs = mlflow.search_runs(
                experiment_ids=[exp.experiment_id],
                filter_string=f"attributes.start_time >= {int(cutoff.timestamp() * 1000)}",
                max_results=50,
            )
            for _, run in runs.iterrows():
                session = self._normalize_mlflow_run(run, exp.name)
                if session is not None:
                    sessions.append(session)

        return sessions

    def _normalize_mlflow_run(self, run, experiment_name: str) -> ProjectSession | None:
        """将 MLflow run 归一化为 ProjectSession。"""
        try:
            run_id = run.get("run_id", "")
            start_time_ms = run.get("attributes.start_time", 0)
            timestamp = datetime.fromtimestamp(start_time_ms / 1000).isoformat()

            # 从 params/metrics 提取工艺上下文
            params = {k.replace("params.", ""): v for k, v in run.items() if k.startswith("params.")}
            metrics = {k.replace("metrics.", ""): v for k, v in run.items() if k.startswith("metrics.")}

            status = run.get("attributes.status", "")
            outcome = "success" if status == "FINISHED" else "failed"

            # AR-02 标记检测
            is_ar_02_pre_fix = params.get("data_version") == "ar_02_pre_fix"

            return ProjectSession(
                session_id=f"mlflow_{run_id}",
                source="mlflow",
                timestamp=timestamp,
                material_type=params.get("material_type"),
                tool_params={
                    "tool_diameter": params.get("tool_diameter"),
                    "tool_type": params.get("tool_type"),
                    "cutting_speed": params.get("cutting_speed"),
                    "feed_rate": params.get("feed_rate"),
                    "depth_of_cut": params.get("depth_of_cut"),
                },
                chatter_confidence=metrics.get("chatter_confidence"),
                predicted_chatter=metrics.get("predicted_chatter"),
                cam_validation_passed=metrics.get("cam_validation_passed"),
                outcome=outcome,
                failure_reason=params.get("failure_reason"),
                is_ar_02_pre_fix=is_ar_02_pre_fix,
                raw_artifact_path=f"mlflow://{experiment_name}/{run_id}",
                metadata={
                    "experiment_name": experiment_name,
                    "status": status,
                    "metrics": metrics,
                    "params": params,
                },
            )
        except Exception as e:
            logger.debug("MLflow run 归一化失败: %s", e)
            return None

    # CAM 验证 report 数据源

    def _extract_cam_sessions(self, cutoff: datetime) -> list[ProjectSession]:
        """从 cam_validation report JSON 提取验证记录。"""
        if not self.cam_reports_dir.exists():
            logger.debug("CAM reports 目录不存在: %s", self.cam_reports_dir)
            return []

        sessions: list[ProjectSession] = []
        cutoff_ts = cutoff.timestamp()

        # 遍历所有 report JSON
        for report_file in self.cam_reports_dir.rglob("*.json"):
            try:
                if report_file.stat().st_mtime < cutoff_ts:
                    continue

                with open(report_file, "r", encoding="utf-8") as f:
                    report = json.load(f)

                session = self._normalize_cam_report(report, str(report_file))
                if session is not None:
                    sessions.append(session)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("跳过 CAM report %s: %s", report_file, e)

        return sessions

    def _normalize_cam_report(self, report: dict[str, Any], file_path: str) -> ProjectSession | None:
        """将 CAM 验证 report 归一化为 ProjectSession。"""
        try:
            timestamp = report.get("timestamp") or report.get("created_at")
            if timestamp is None:
                # 用文件修改时间兜底
                timestamp = datetime.fromtimestamp(Path(file_path).stat().st_mtime).isoformat()

            passed = report.get("passed") or report.get("validation_passed")
            failure_reason = report.get("failure_reason") or report.get("errors", [None])[0]

            return ProjectSession(
                session_id=f"cam_{report.get('report_id', Path(file_path).stem)}",
                source="cam_validation",
                timestamp=timestamp,
                material_type=report.get("material_type"),
                tool_params=report.get("tool_params", {}),
                cam_validation_passed=passed,
                cam_validation_failure_reason=failure_reason,
                outcome="success" if passed else "failure",
                failure_reason=failure_reason,
                raw_artifact_path=file_path,
                metadata={
                    "controller": report.get("controller"),
                    "gcode_path": report.get("gcode_path"),
                    "disclaimer": report.get("disclaimer"),
                },
            )
        except Exception as e:
            logger.debug("CAM report 归一化失败: %s", e)
            return None

    # 审计日志数据源

    def _extract_audit_sessions(self, cutoff: datetime) -> list[ProjectSession]:
        """从 audit_log 哈希链提取决策记录。"""
        if not self.audit_log_dir.exists():
            logger.debug("Audit log 目录不存在: %s", self.audit_log_dir)
            return []

        sessions: list[ProjectSession] = []
        cutoff_ms = int(cutoff.timestamp() * 1000)

        # 审计日志按日期组织：audit/{YYYY-MM-DD}/audit.log
        for date_dir in self.audit_log_dir.iterdir():
            if not date_dir.is_dir():
                continue

            log_file = date_dir / "audit.log"
            if not log_file.exists():
                continue

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if entry.get("timestamp_ms", 0) < cutoff_ms:
                            continue

                        session = self._normalize_audit_entry(entry, str(log_file))
                        if session is not None:
                            sessions.append(session)
            except OSError as e:
                logger.debug("读取 audit log %s 失败: %s", log_file, e)

        return sessions

    def _normalize_audit_entry(self, entry: dict[str, Any], log_path: str) -> ProjectSession | None:
        """将审计日志条目归一化为 ProjectSession。"""
        try:
            timestamp_ms = entry.get("timestamp_ms", 0)
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000).isoformat()

            operation_status = entry.get("operation_status", "")
            outcome_map = {
                "success": "success",
                "failed": "failure",
                "cancelled": "warning",
                "pending": "unknown",
            }
            outcome = outcome_map.get(operation_status, "unknown")

            ai_recommendation = entry.get("ai_recommendation", {})

            return ProjectSession(
                session_id=f"audit_{entry.get('chain_seq', '')}",
                source="audit_log",
                timestamp=timestamp,
                material_type=ai_recommendation.get("material_type"),
                tool_params=ai_recommendation.get("tool_params", {}),
                chatter_confidence=entry.get("confidence"),
                outcome=outcome,
                failure_reason=ai_recommendation.get("failure_reason"),
                raw_artifact_path=log_path,
                metadata={
                    "ai_module": entry.get("ai_module"),
                    "user_decision": entry.get("user_decision"),
                    "chain_seq": entry.get("chain_seq"),
                    "entry_hash": entry.get("entry_hash"),
                    "reasoning": entry.get("reasoning"),
                },
            )
        except Exception as e:
            logger.debug("Audit entry 归一化失败: %s", e)
            return None

    # 切削参数任务数据源

    def _extract_cutting_sessions(self, cutoff: datetime) -> list[ProjectSession]:
        """从 cutting_store 提取切削参数推荐任务。"""
        if self.cutting_store is None:
            return []

        sessions: list[ProjectSession] = []

        try:
            # 调用 cutting_store 的列表接口（假设有 list_tasks 方法）
            if hasattr(self.cutting_store, "list_tasks"):
                tasks = self.cutting_store.list_tasks()
            elif hasattr(self.cutting_store, "get_all_tasks"):
                tasks = self.cutting_store.get_all_tasks()
            else:
                logger.debug("cutting_store 无 list/get_all 方法，跳过")
                return []

            for task in tasks:
                # 过滤时间
                created_at = task.get("created_at") or task.get("created_time")
                if created_at is None:
                    continue
                try:
                    task_time = datetime.fromisoformat(created_at.replace("Z", ""))
                except (ValueError, TypeError):
                    continue
                if task_time < cutoff:
                    continue

                status = task.get("status", "")
                # 状态机映射
                outcome_map = {
                    "SUCCEEDED": "success",
                    "FAILED": "failure",
                    "CANCELLED": "warning",
                    "PARAMS_RECOMMENDED": "unknown",
                    "REVIEWED": "unknown",
                    "PENDING": "unknown",
                    "RUNNING": "unknown",
                }
                outcome = outcome_map.get(status, "unknown")

                # 硬约束标记：SUCCEEDED 禁删
                is_locked_succeeded = status == "SUCCEEDED"

                sessions.append(
                    ProjectSession(
                        session_id=f"cutting_{task.get('task_id', '')}",
                        source="cutting_store",
                        timestamp=created_at,
                        material_type=task.get("material_type"),
                        tool_params=task.get("tool_params", {}),
                        outcome=outcome,
                        failure_reason=task.get("failure_reason"),
                        raw_artifact_path=task.get("task_id"),
                        metadata={
                            "status": status,
                            "is_locked_succeeded": is_locked_succeeded,
                            "review_status": task.get("review_status"),
                            "cutting_params": task.get("cutting_params"),
                        },
                    )
                )
        except Exception as e:
            logger.warning("Cutting store 提取失败: %s", e)

        return sessions
