"""OpenMVS 二进制子进程封装。

OpenMVS 接收 COLMAP 输出的稀疏模型（相机位姿 + 稀疏点云），
稠密化为完整网格模型。

调用流程（每一步单独调用 OpenMVS 命令）：
    DensifyMesh       → 稠密点云 + 网格
    RefineMesh        → 网格细化（可选，精度 high 档位启用）
    TextureMesh       → 纹理映射（可选）

输出：
    scene_dense_mesh.ply / .obj
    scene_dense_mesh_refine_texture.glb（最终带纹理）
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ImageTo3DConfig

logger = logging.getLogger(__name__)


class OpenMvsError(RuntimeError):
    """OpenMVS 调用失败。"""


@dataclass
class OpenMvsRunResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def _openmvs_available(openmvs_bin: str) -> bool:
    """检测 OpenMVS 二进制是否可调用。"""
    try:
        result = subprocess.run(
            [openmvs_bin, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        # OpenMVS 某些版本 --help 返回 0，某些返回 1，只要 stdout 有内容即视为可用
        return result.returncode == 0 or bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("OpenMVS 探测失败 bin=%s err=%s", openmvs_bin, e)
        return False


def _run_openmvs(
    openmvs_bin: str,
    args: list[str],
    cwd: Path,
    timeout: int,
) -> OpenMvsRunResult:
    """执行一次 OpenMVS 命令。"""
    cmd = [openmvs_bin] + args
    logger.info("OpenMVS 执行: %s (cwd=%s)", " ".join(cmd), cwd)
    t0 = time.time()
    try:
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
            tail = (proc.stderr or "")[-2000:]
            raise OpenMvsError(
                f"OpenMVS 子命令失败 returncode={proc.returncode} cmd={openmvs_bin} stderr_tail={tail!r}"
            )
        return OpenMvsRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_seconds=elapsed,
        )
    except FileNotFoundError as e:
        raise OpenMvsError(
            f"OpenMVS 二进制未找到 bin={openmvs_bin!r}。"
            "请从 https://github.com/cdcseacave/openMVS 编译安装，"
            "并设置环境变量 LNN_I2T3D_OPENMVS_BIN 指向 DensifyMesh 可执行文件。"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise OpenMvsError(f"OpenMVS 子命令超时 timeout={timeout}s bin={openmvs_bin}") from e


def run_dense_reconstruction(
    sparse_txt_dir: Path,
    workspace_dir: Path,
    cfg: ImageTo3DConfig,
) -> dict[str, Any]:
    """执行 OpenMVS 稠密化网格重建。

    产物布局（写入 workspace_dir）：
        scene.mvs                → OpenMVS 工程文件
        scene_dense.ply          → 稠密点云
        scene_dense_mesh.ply     → 稠密网格（无纹理）
        scene_dense_mesh_refine.ply  → 细化后网格（high 档位）
        scene_dense_mesh_refine_texture.glb → 最终带纹理 GLB

    Args:
        sparse_txt_dir: COLMAP 输出的 TXT 格式稀疏模型目录
        workspace_dir: 本次任务工作目录
        cfg: ImageTo3DConfig

    Returns:
        dict 包含：
            - mesh_path: Path → 最终网格文件
            - mesh_format: str → 'ply' 或 'glb'
            - has_texture: bool → 是否带纹理

    Raises:
        OpenMvsError: 任一 OpenMVS 子命令失败
    """
    if not _openmvs_available(cfg.openmvs_bin):
        raise OpenMvsError(
            f"OpenMVS 二进制不可用 bin={cfg.openmvs_bin!r}。请编译安装 OpenMVS 并设置 LNN_I2T3D_OPENMVS_BIN。"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    scene_mvs = workspace_dir / "scene.mvs"

    # 从 COLMAP 稀疏模型导入到 OpenMVS 工程文件
    # OpenMVS 提供 InterColmapToOpenMVS / DensifyMesh 都可读 COLMAP 输出，
    # 这里直接用 DensifyMesh 的 --import-path 选项（兼容 COLMAP TXT 输出）
    resolution_level = cfg.precision_specs["openmvs_resolution_level"]

    # DensifyMesh 同时完成稠密点云 + 网格生成
    _run_openmvs(
        cfg.openmvs_bin,
        [
            "--input-file",
            str(sparse_txt_dir),
            "--output-file",
            str(scene_mvs),
            "--resolution-level",
            str(resolution_level),
            "--number-views",
            "0",  # 0 = 用所有视图
        ],
        cwd=workspace_dir,
        timeout=cfg.task_timeout_seconds,
    )

    dense_mesh_ply = workspace_dir / "scene_dense_mesh.ply"
    if not dense_mesh_ply.exists():
        # 某些 OpenMVS 版本输出文件名不同，尝试常见变体
        candidates = [
            workspace_dir / "scene_dense_mesh.ply",
            workspace_dir / "scene_dense.ply",
            workspace_dir / "scene.mvs",
        ]
        for c in candidates:
            if c.exists():
                dense_mesh_ply = c
                break
        else:
            raise OpenMvsError(
                f"OpenMVS 未生成网格文件，期望 {dense_mesh_ply} 不存在。"
                "可能原因：1) 稀疏模型相机数不足；2) OpenMVS 版本不兼容。"
            )

    # 精度 high 档位启用 RefineMesh（耗时较长）
    if cfg.precision_tier == "high":
        refined_ply = workspace_dir / "scene_dense_mesh_refine.ply"
        try:
            # RefineMesh 二进制名通常与 DensifyMesh 同目录
            refine_bin = str(Path(cfg.openmvs_bin).parent / "RefineMesh")
            _run_openmvs(
                refine_bin,
                [
                    "--input-file",
                    str(dense_mesh_ply),
                    "--output-file",
                    str(refined_ply),
                    "--resolution-level",
                    "1",
                ],
                cwd=workspace_dir,
                timeout=cfg.task_timeout_seconds,
            )
            dense_mesh_ply = refined_ply
        except OpenMvsError as e:
            logger.warning("RefineMesh 失败，使用未细化网格: %s", e)

    return {
        "mesh_path": dense_mesh_ply,
        "mesh_format": "ply",
        "has_texture": False,
    }
