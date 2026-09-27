"""MLflow 实验追踪工具（可选依赖）。

学术要求：
- 论文报告的每个实验指标必须有对应的运行记录
- 审稿人可以通过 MLflow 验证报告数字
- 超参数-指标关联可追溯

设计：
- MLflow 未安装时优雅降级，所有方法为空操作
- 默认存储到 data/mlruns/（本地化）
- 自动记录参数、指标、模型
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 默认 MLflow 存储路径（本地化，无云依赖）。
# 用 as_uri() 而非手工拼 file:// 前缀：Windows 下 os.path.abspath 产生
# 反斜杠路径，拼出来的 file://D:\... 会被 mlflow 的 URI 解析器拒绝
# （MlflowException: not a valid remote uri）；as_uri() 生成规范的
# file:///D:/... 正斜杠形式，三平台通用。
_DEFAULT_TRACKING_URI = Path("data/mlruns").resolve().as_uri()
_DEFAULT_EXPERIMENT_NAME = "LNN_LiquidNeuralNetwork"

# 检测 MLflow 是否可用
try:
    import mlflow

    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False
    logger.info("MLflow 未安装，实验追踪已禁用。安装方式: pip install mlflow")


@contextmanager
def start_run(
    run_name: Optional[str] = None,
    experiment_name: str = _DEFAULT_EXPERIMENT_NAME,
    tracking_uri: Optional[str] = None,
) -> Iterator[Any]:
    """启动一个 MLflow run 的上下文管理器。

    MLflow 未安装时为空操作上下文。

    用法：
        with start_run(run_name="LTC_uniwear_001") as run:
            log_params({"learning_rate": 0.001, "seed": 42})
            log_metric("val_loss", 0.05, step=epoch)
            log_model(model, "model")
    """
    if not HAS_MLFLOW:
        yield None
        return

    uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", _DEFAULT_TRACKING_URI)
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(experiment_name)

    # 残留的活动 run(脚本/测试异常退出没走 __exit__)会让 start_run 直接抛
    # "Run already active";开新 run 前先收掉。
    stale = mlflow.active_run()
    if stale is not None:
        logger.warning("发现未关闭的 MLflow run(%s),自动结束后再开新 run", stale.info.run_id)
        mlflow.end_run()

    with mlflow.start_run(run_name=run_name) as run:
        yield run


def log_params(params: Dict[str, Any]) -> None:
    """记录实验参数。MLflow 未安装时空操作。"""
    if not HAS_MLFLOW:
        return
    try:
        # mlflow log_params 仅支持基础类型，复杂值转为字符串
        safe_params: Dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, (int, float, str, bool)):
                safe_params[str(key)] = value
            else:
                safe_params[str(key)] = str(value)
        mlflow.log_params(safe_params)
    except Exception as e:
        logger.warning("MLflow log_params 失败: %s", e)


def log_metric(key: str, value: float, step: Optional[int] = None) -> None:
    """记录实验指标。MLflow 未安装时空操作。"""
    if not HAS_MLFLOW:
        return
    try:
        mlflow.log_metric(key, float(value), step=step)
    except Exception as e:
        logger.warning("MLflow log_metric 失败: %s", e)


def log_metrics(metrics: Dict[str, float], step: Optional[int] = None) -> None:
    """批量记录实验指标。"""
    if not HAS_MLFLOW:
        return
    try:
        safe_metrics: Dict[str, float] = {}
        for key, value in metrics.items():
            try:
                safe_metrics[str(key)] = float(value)
            except (ValueError, TypeError):
                logger.debug("跳过无法转为 float 的指标 %s=%r", key, value)
        if safe_metrics:
            mlflow.log_metrics(safe_metrics, step=step)
    except Exception as e:
        logger.warning("MLflow log_metrics 失败: %s", e)


def log_model(model: Any, artifact_path: str = "model") -> None:
    """记录模型权重。

    优先使用 PyTorch flavor；非 PyTorch 模型回退到 state_dict 或 pickle。
    """
    if not HAS_MLFLOW:
        return
    try:
        # 尝试用 PyTorch flavor
        import mlflow.pytorch

        try:
            import torch.nn as nn

            if isinstance(model, nn.Module):
                mlflow.pytorch.log_model(model, artifact_path)
                logger.info("MLflow 已记录 PyTorch 模型到 %s", artifact_path)
                return
        except ImportError:
            pass

        # 非 PyTorch 模型：尝试 state_dict + torch.save
        try:
            import tempfile
            import torch

            tmp_path: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
                    tmp_path = f.name
                    if hasattr(model, "state_dict"):
                        torch.save(model.state_dict(), f.name)
                    else:
                        torch.save(model, f.name)
                    mlflow.log_artifact(f.name, artifact_path)
                logger.info("MLflow 已记录模型 artifact 到 %s", artifact_path)
            finally:
                # P1-2 修复：无论 mlflow.log_artifact 是否抛异常都必须清理临时文件，
                # 否则长训练任务会泄漏大量 .pt 文件填满磁盘。
                if tmp_path is not None:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        except Exception:
            # 最终回退到 pickle
            import pickle
            import tempfile

            pkl_path: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
                    pkl_path = f.name
                    pickle.dump(model, f)
                    mlflow.log_artifact(f.name, artifact_path)
                logger.info("MLflow 已记录模型 pickle artifact 到 %s", artifact_path)
            finally:
                if pkl_path is not None:
                    try:
                        os.unlink(pkl_path)
                    except OSError:
                        pass
    except Exception as e:
        logger.warning("MLflow log_model 失败: %s", e)


def log_artifact(local_path: str, artifact_path: Optional[str] = None) -> None:
    """记录任意文件（如预处理器）。"""
    if not HAS_MLFLOW:
        return
    try:
        mlflow.log_artifact(local_path, artifact_path)
    except Exception as e:
        logger.warning("MLflow log_artifact 失败: %s", e)


def is_enabled() -> bool:
    """返回 MLflow 是否可用。"""
    return HAS_MLFLOW
