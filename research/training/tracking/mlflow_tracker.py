"""MLflow Experiment Tracker — 软依赖封装。

学术诚信要求：
- 训练超参数（lr, batch_size, seed, model_config 等）必须记录
- 每个 epoch 的 metrics（loss, val_loss, mae 等）必须记录
- 最终模型 artifact 必须记录
- 如果 mlflow 未安装，记录 warning 但不崩溃（软依赖）

用法示例::

    tracker = MLflowTracker(tracking_uri="./mlruns")
    tracker.start_run(experiment_name="lnn_cfc", run_name="seed42_lr001")
    tracker.log_params({"learning_rate": 0.001, "seed": 42, ...})
    for epoch in range(epochs):
        tracker.log_metrics({"loss": 0.5, "val_loss": 0.6, "mae": 0.3}, step=epoch)
    tracker.log_model(model, artifact_path="model")
    tracker.end_run()

也可以使用 contextmanager::

    with mlflow_run(tracker, experiment_name="lnn", run_name="exp1"):
        tracker.log_params({...})
        tracker.log_metrics({...})
"""

import logging
from contextlib import contextmanager
from typing import Any, Dict, Optional, Iterator

logger = logging.getLogger(__name__)

# 尝试导入 mlflow（软依赖）
try:
    import mlflow
    import mlflow.pytorch

    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False
    mlflow = None  # type: ignore


class MLflowTracker:
    """MLflow 实验追踪封装器。

    所有方法在 mlflow 未安装时安全降级为 no-op 并记录 warning，
    不会抛出异常影响训练主流程。

    Args:
        tracking_uri: MLflow tracking URI，默认为本地 ``./mlruns``。
            生产环境可配置为 PostgreSQL/MySQL 后端或 MLflow Server URL。
    """

    def __init__(self, tracking_uri: str = "./mlruns"):
        self.tracking_uri = tracking_uri
        self._active_run: Optional[Any] = None

        if not HAS_MLFLOW:
            logger.warning(
                "mlflow 未安装，实验追踪功能已禁用。"
                "请运行 `pip install mlflow>=2.0` 启用实验追踪。"
                "训练将继续正常执行，但超参数和指标不会被记录。"
            )
        else:
            mlflow.set_tracking_uri(tracking_uri)
            logger.info("MLflow tracking URI 设置为: %s", tracking_uri)

    @property
    def is_available(self) -> bool:
        """mlflow 是否可用（已安装且成功导入）。"""
        return HAS_MLFLOW

    def start_run(
        self,
        experiment_name: str = "lnn_default",
        run_name: Optional[str] = None,
    ) -> None:
        """启动一次 MLflow run。

        如果 mlflow 不可用，则为 no-op。

        Args:
            experiment_name: 实验名称（不存在则自动创建）。
            run_name: 本次 run 的名称标识。
        """
        if not HAS_MLFLOW:
            logger.debug("mlflow 不可用，跳过 start_run")
            return

        try:
            mlflow.set_experiment(experiment_name)
            self._active_run = mlflow.start_run(run_name=run_name)
            logger.info(
                "MLflow run 已启动: experiment=%s, run_name=%s, run_id=%s",
                experiment_name,
                run_name,
                self._active_run.info.run_id,
            )
        except Exception as exc:
            logger.warning("MLflow start_run 失败，追踪降级为 no-op: %s", exc, exc_info=True)
            self._active_run = None

    def log_params(self, params: Dict[str, Any]) -> None:
        """记录超参数到当前 MLflow run。

        常见参数包括：learning_rate, batch_size, seed, optimizer_type,
        loss_type, epochs, model_config 等。

        Args:
            params: 超参数键值对字典。
        """
        if not HAS_MLFLOW or self._active_run is None:
            logger.debug("mlflow 不可用或无 active run，跳过 log_params")
            return

        try:
            # mlflow log_param 仅支持基础类型，复杂值转为字符串
            safe_params: Dict[str, str] = {}
            for key, value in params.items():
                if isinstance(value, (int, float, str, bool)):
                    safe_params[str(key)] = value
                else:
                    safe_params[str(key)] = str(value)

            mlflow.log_params(safe_params)
            logger.debug("MLflow 已记录 %d 个参数", len(safe_params))
        except Exception as exc:
            logger.warning("MLflow log_params 失败: %s", exc, exc_info=True)

    def log_metrics(
        self,
        metrics: Dict[str, float],
        step: Optional[int] = None,
    ) -> None:
        """记录训练/验证指标到当前 MLflow run。

        常见指标包括：loss, val_loss, mae, train_accuracy, val_accuracy,
        learning_rate 等。

        Args:
            metrics: 指标键值对字典（值必须为 float 或可转为 float）。
            step: 当前 epoch/step，用于绘制指标曲线。
        """
        if not HAS_MLFLOW or self._active_run is None:
            logger.debug("mlflow 不可用或无 active run，跳过 log_metrics")
            return

        try:
            # mlflow log_metric 仅支持 float 值
            safe_metrics: Dict[str, float] = {}
            for key, value in metrics.items():
                try:
                    safe_metrics[str(key)] = float(value)
                except (ValueError, TypeError):
                    logger.debug("跳过无法转为 float 的指标 %s=%r", key, value)

            if safe_metrics:
                mlflow.log_metrics(safe_metrics, step=step)
        except Exception as exc:
            logger.warning("MLflow log_metrics 失败: %s", exc, exc_info=True)

    def log_model(self, model: Any, artifact_path: str = "model") -> None:
        """记录训练完成的模型作为 MLflow artifact。

        使用 ``mlflow.pytorch.log_model`` 保存 PyTorch 模型。
        如果模型不是 PyTorch Module，尝试使用 ``mlflow.log_artifact``。

        Args:
            model: 训练完成的模型对象。
            artifact_path: artifact 在 run 内的相对路径。
        """
        if not HAS_MLFLOW or self._active_run is None:
            logger.debug("mlflow 不可用或无 active run，跳过 log_model")
            return

        try:
            # 尝试使用 PyTorch flavor
            try:
                import torch.nn as nn

                if isinstance(model, nn.Module):
                    mlflow.pytorch.log_model(model, artifact_path)
                    logger.info("MLflow 已记录 PyTorch 模型到 %s", artifact_path)
                    return
            except ImportError:
                pass

            # 非 PyTorch 模型：尝试直接记录 artifact
            import tempfile
            import os

            with tempfile.TemporaryDirectory() as tmpdir:
                model_path = os.path.join(tmpdir, "model.pkl")
                try:
                    import pickle

                    with open(model_path, "wb") as f:
                        pickle.dump(model, f)
                    mlflow.log_artifact(model_path, artifact_path=artifact_path)
                    logger.info("MLflow 已记录模型 artifact 到 %s", artifact_path)
                except Exception as exc:
                    logger.warning("MLflow log_model (pickle fallback) 失败: %s", exc)
        except Exception as exc:
            logger.warning("MLflow log_model 失败: %s", exc, exc_info=True)

    def log_artifact(self, local_path: str, artifact_path: Optional[str] = None) -> None:
        """记录本地文件作为 artifact（如检查点文件、配置文件）。

        Args:
            local_path: 本地文件路径。
            artifact_path: artifact 在 run 内的子目录。
        """
        if not HAS_MLFLOW or self._active_run is None:
            logger.debug("mlflow 不可用或无 active run，跳过 log_artifact")
            return

        try:
            mlflow.log_artifact(local_path, artifact_path=artifact_path)
        except Exception as exc:
            logger.warning("MLflow log_artifact 失败: %s", exc, exc_info=True)

    def end_run(self) -> None:
        """结束当前 MLflow run。

        如果没有 active run 或 mlflow 不可用，则为 no-op。
        """
        if not HAS_MLFLOW:
            logger.debug("mlflow 不可用，跳过 end_run")
            return

        if self._active_run is not None:
            try:
                mlflow.end_run()
                logger.info("MLflow run 已结束: run_id=%s", self._active_run.info.run_id)
            except Exception as exc:
                logger.warning("MLflow end_run 失败: %s", exc, exc_info=True)
            finally:
                self._active_run = None
        else:
            # 兜底：确保任何 stray run 被关闭
            try:
                mlflow.end_run()
            except Exception as exc:
                # P1-4 修复：不得静默吞没异常，否则 MLflow 服务端连接泄漏无法排查。
                # stray run 关闭失败通常是网络/存储问题，记录 warning 即可，
                # 不影响业务流程（本分支已是兜底路径）。
                logger.warning(
                    "MLflow stray end_run 失败: %s", exc, exc_info=True
                )


@contextmanager
def mlflow_run(
    tracker: MLflowTracker,
    experiment_name: str = "lnn_default",
    run_name: Optional[str] = None,
) -> Iterator[MLflowTracker]:
    """Context manager 确保 MLflow run 正确关闭。

    用法::

        tracker = MLflowTracker()
        with mlflow_run(tracker, experiment_name="exp1", run_name="run1"):
            tracker.log_params({...})
            tracker.log_metrics({...})

    Args:
        tracker: MLflowTracker 实例。
        experiment_name: 实验名称。
        run_name: run 名称。

    Yields:
        传入的 tracker 实例。
    """
    tracker.start_run(experiment_name=experiment_name, run_name=run_name)
    try:
        yield tracker
    finally:
        tracker.end_run()
