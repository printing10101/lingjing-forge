"""COLMAP 二进制子进程封装。

灵境制造采用「外部二进制 + subprocess」而非 pycolmap，原因：
1. COLMAP 官方推荐的 Windows 安装方式是下载预编译二进制，pycolmap 在 Windows
   上构建链路复杂，常因 CUDA/SuiteSparse 缺失导致 import 失败。
2. 调用二进制后无需将 COLMAP 编译进 sidecar PyInstaller 包，便于瘦身分发。
3. 工程实践：COLMAP 命令行接口稳定，跨版本兼容性好。

调用流程（每一步都通过 subprocess 单独调用 colmap 命令）：
    feature_extractor     → 提取 SIFT 特征
    matcher_exhaustive    → 全配对特征匹配（照片少时用 exhaustive 最稳）
    mapper                → 增量式 SfM，输出稀疏点云 + 相机位姿（binary）
    model_converter       → 将稀疏模型转成 TXT/PLY 便于 OpenMVS 读取
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ImageTo3DConfig
import time

logger = logging.getLogger(__name__)


class ColmapError(RuntimeError):
    """COLMAP 调用失败。"""


@dataclass
class ColmapRunResult:
    """COLMAP 一次调用结果。"""

    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def _colmap_available(colmap_bin: str) -> bool:
    """检测 COLMAP 二进制是否可调用。

    使用 `colmap help` 探测，returncode=0 视为可用。
    Windows 路径含空格时由调用方负责加引号（本函数已处理）。
    """
    try:
        result = subprocess.run(
            [colmap_bin, "help"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("COLMAP 探测失败 bin=%s err=%s", colmap_bin, e)
        return False


def _run_colmap(
    colmap_bin: str,
    args: list[str],
    cwd: Path,
    timeout: int,
) -> ColmapRunResult:
    """执行一次 colmap 命令并捕获输出。

    Args:
        colmap_bin: colmap 可执行文件路径
        args: 传给 colmap 的子命令与参数（如 ["feature_extractor", "--image_path", ...]）
        cwd: 工作目录（COLMAP 习惯把产物写在工作目录下）
        timeout: 超时秒数

    Returns:
        ColmapRunResult

    Raises:
        ColmapError: 当 returncode != 0 或子进程异常时
    """
    cmd = [colmap_bin] + args
    logger.info("COLMAP 执行: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        t0 = time.time()
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        elapsed = time.time() - t0
        if proc.returncode != 0:
            # 仅截取最后 2000 字符，避免日志爆炸
            tail = (proc.stderr or "")[-2000:]
            raise ColmapError(
                f"COLMAP 子命令失败 returncode={proc.returncode} cmd={' '.join(args[:2])} stderr_tail={tail!r}"
            )
        return ColmapRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_seconds=elapsed,
        )
    except FileNotFoundError as e:
        raise ColmapError(
            f"COLMAP 二进制未找到 bin={colmap_bin!r}。"
            "请从 https://colmap.github.io/install.html 下载并配置 LNN_I2T3D_COLMAP_BIN"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ColmapError(f"COLMAP 子命令超时 timeout={timeout}s cmd={' '.join(args[:2])}") from e


def run_sparse_reconstruction(
    image_dir: Path,
    workspace_dir: Path,
    cfg: ImageTo3DConfig,
) -> dict[str, Any]:
    """执行完整 COLMAP 稀疏重建流程。

    产物布局（写入 workspace_dir）：
        images/        → 软链或拷贝的输入图片
        database.db    → SQLite 特征数据库
        sparse/0/      → 增量式 SfM 输出（含 cameras.bin / images.bin / points3D.bin）
        sparse_txt/    → 文本格式模型，供 OpenMVS 读取
        sparse.ply     → 稀疏点云 PLY（便于人眼检查）

    Args:
        image_dir: 输入照片目录（已被 sanitize 过的）
        workspace_dir: 本次任务的工作目录
        cfg: ImageTo3DConfig

    Returns:
        dict 包含：
            - model_dir: Path → sparse_txt 目录（OpenMVS 入口）
            - sparse_ply: Path → sparse.ply
            - num_images_registered: int → 成功注册的相机数
            - feature_threshold: int → 当前精度档位特征数阈值

    Raises:
        ColmapError: 任一 COLMAP 子命令失败
    """
    if not _colmap_available(cfg.colmap_bin):
        raise ColmapError(
            f"COLMAP 二进制不可用 bin={cfg.colmap_bin!r}。"
            "请安装 COLMAP 并设置环境变量 LNN_I2T3D_COLMAP_BIN 指向其可执行文件路径。"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    db_path = workspace_dir / "database.db"
    sparse_dir = workspace_dir / "sparse"
    sparse_dir.mkdir(parents=True, exist_ok=True)
    sparse_txt_dir = workspace_dir / "sparse_txt"
    sparse_txt_dir.mkdir(parents=True, exist_ok=True)
    sparse_ply = workspace_dir / "sparse.ply"

    # 当前精度档位对应的 SIFT 特征数阈值
    feature_threshold = cfg.precision_specs["colmap_feature_threshold"]

    # 1. 特征提取
    _run_colmap(
        cfg.colmap_bin,
        [
            "feature_extractor",
            "--database_path",
            str(db_path),
            "--image_path",
            str(image_dir),
            "--ImageReader.single_camera",
            "1",
            "--ImageReader.camera_model",
            "SIMPLE_PINHOLE",
            # SIFT 数量上限由精度档位控制
            "--SiftExtraction.max_image_size",
            "1600" if cfg.precision_tier == "coarse" else "2000",
            "--SiftExtraction.max_num_features",
            str(feature_threshold),
            "--SiftExtraction.estimate_affine_shape",
            "1" if cfg.precision_tier == "high" else "0",
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    # 2. 全配对匹配（照片数 ≤ 200 用 exhaustive 最稳）
    _run_colmap(
        cfg.colmap_bin,
        [
            "matcher_exhaustive",
            "--database_path",
            str(db_path),
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    # 3. 增量式 SfM
    _run_colmap(
        cfg.colmap_bin,
        [
            "mapper",
            "--database_path",
            str(db_path),
            "--image_path",
            str(image_dir),
            "--output_path",
            str(sparse_dir),
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    # 检查稀疏模型 0 是否生成
    model_bin_dir = sparse_dir / "0"
    if not (model_bin_dir / "cameras.bin").exists():
        raise ColmapError(
            "COLMAP mapper 未生成稀疏模型（sparse/0/cameras.bin 缺失）。"
            "可能原因：1) 照片特征点不足；2) 照片覆盖角度不够；3) 照片模糊或光照差异过大。"
        )

    # 4. 转换为 TXT 格式，便于 OpenMVS 读取
    _run_colmap(
        cfg.colmap_bin,
        [
            "model_converter",
            "--input_path",
            str(model_bin_dir),
            "--output_path",
            str(sparse_txt_dir),
            "--output_type",
            "TXT",
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    # 5. 导出稀疏点云 PLY（便于人眼检查重建质量）
    _run_colmap(
        cfg.colmap_bin,
        [
            "model_converter",
            "--input_path",
            str(model_bin_dir),
            "--output_path",
            str(sparse_ply),
            "--output_type",
            "PLY",
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    # 统计注册的相机数（读 images.txt 行数 / 2，每张图占 2 行）
    images_txt = sparse_txt_dir / "images.txt"
    num_registered = 0
    if images_txt.exists():
        try:
            with images_txt.open("r", encoding="utf-8") as f:
                # 跳过注释行，统计非空行 / 2
                lines = [ln for ln in f if ln.strip() and not ln.startswith("#")]
                num_registered = len(lines) // 2
        except OSError as e:
            logger.warning("读取 images.txt 失败: %s", e)

    return {
        "model_dir": sparse_txt_dir,
        "sparse_ply": sparse_ply,
        "num_images_registered": num_registered,
        "feature_threshold": feature_threshold,
    }
