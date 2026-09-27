"""torch 环境就绪一键验证脚本（ADR-020 思路 1-3 P2 解锁验证）.

用途：
    在 torch 环境部署完成后，一键跑全所有经 ``pytest.importorskip("torch")``
    自然跳过的测试用例，验证 ADR-020 思路 1-3 的 torch 依赖代码路径全部可用。

    本脚本服务于 ADR-020 P2「torch 环境部署」的验收环节：
        L4 环境阻塞解除后，本地累计 ``N skipped`` 应降为 ``0 skipped``。

用法：
    cd python
    python scripts/verify_torch_ready.py

前置条件：
    1. torch 已安装（``pip install torch`` 或 ``conda install pytorch``）
    2. WinSock 已修复（管理员运行 ``netsh winsock reset`` + 重启），
       或通过 ``run_pytest.py`` 的绕过补丁跑测试

退出码：
    0 — torch 可用且全部目标测试通过（skipped=0）
    1 — torch 不可用（提示安装步骤）
    2 — torch 可用但有测试失败或仍被 skip（需排查）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ADR-020 思路 1-3 所有 torch 依赖测试文件（经 grep importorskip("torch") 确认）
# 排除 tests/conftest.py（fixtures 容器，非独立测试）
TORCH_DEPENDENT_TESTS: list[str] = [
    # 思路 1：统一表示异质对象（融合架构）
    "tests/plugins/world_model/test_fusion_trainer.py",
    "tests/plugins/world_model/test_plugin_weights_resolution.py",
    "tests/plugins/world_model/test_unified_state_assembler.py",
    "tests/plugins/world_model/test_fusion_integration.py",
    "tests/plugins/world_model/test_unified_state.py",
    # 思路 2-3：零件专属先验 + 几何一致性约束
    "tests/image_to_3d/test_part_prior.py",
    "tests/image_to_3d/test_geometry_loss.py",
    # LNN 引擎 torch 依赖路径
    "tests/unit/test_lnn_trainer.py",
]


def check_torch_available() -> tuple[bool, str]:
    """检测 torch 是否可用，返回 (可用性, 版本信息字符串)."""
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as exc:
        return False, f"torch 导入失败: {exc}"

    info = f"torch {torch.__version__}"
    try:
        cuda_ok = torch.cuda.is_available()
        info += f" | cuda_available={cuda_ok}"
        if cuda_ok:
            info += f" | {torch.cuda.get_device_name(0)}"
    except Exception as exc:  # noqa: BLE001
        info += f" | cuda 检测异常: {exc}"
    return True, info


def run_tests() -> int:
    """通过 run_pytest.py 跑全 torch 依赖测试（复用 WinSock 绕过补丁）.

    Returns
    -------
    int
        pytest 退出码。
    """
    python_dir = Path(__file__).resolve().parent.parent  # python/
    run_pytest = python_dir / "run_pytest.py"

    if not run_pytest.exists():
        print(f"[verify] 未找到 {run_pytest}，回退到 ``python -m pytest``", file=sys.stderr)
        cmd = [sys.executable, "-m", "pytest"]
    else:
        cmd = [sys.executable, str(run_pytest)]

    # -o addopts="" 清空 pytest.ini 的 --cov 配置（pytest-cov 未安装时必需）
    # --noconftest 绕过 conftest.py 触发的 slowapi 不可用问题
    # -v 显示详细用例名，便于定位失败点
    cmd.extend(
        [
            "-o",
            "addopts=",
            "--noconftest",
            "-v",
            *TORCH_DEPENDENT_TESTS,
        ]
    )

    print(f"[verify] 执行: {' '.join(cmd)}", file=sys.stderr)
    print(f"[verify] 工作目录: {python_dir}", file=sys.stderr)
    return subprocess.call(cmd, cwd=str(python_dir))


def main() -> int:
    print("=" * 70, file=sys.stderr)
    print("ADR-020 torch 环境就绪验证", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    # 1. 检测 torch
    torch_ok, torch_info = check_torch_available()
    print(f"[verify] {torch_info}", file=sys.stderr)

    if not torch_ok:
        print(
            "\n[verify] torch 不可用。请按 ADR-020 P2 SOP 完成 torch 环境部署：\n"
            "  1. 管理员运行 ``netsh winsock reset`` 并重启系统（修复 WinError 10038）\n"
            "  2. ``pip install torch`` 或 ``conda install pytorch cpuonly -c pytorch``\n"
            "  3. 重新运行 ``python scripts/verify_torch_ready.py``\n",
            file=sys.stderr,
        )
        return 1

    # 2. 跑全 torch 依赖测试
    print(
        f"\n[verify] torch 可用，开始跑全 {len(TORCH_DEPENDENT_TESTS)} 个测试文件的 "
        "torch 依赖用例...\n",
        file=sys.stderr,
    )
    exit_code = run_tests()

    # 3. 结果判定
    print("\n" + "=" * 70, file=sys.stderr)
    if exit_code == 0:
        print(
            "[verify] 全部 torch 依赖测试通过 — ADR-020 P2 L4 环境阻塞已解除\n"
            "   下一步可推进：\n"
            "   - PHM2010 全链路跑通（思路 1 验收）\n"
            "   - MLflow 记录 fusion_layer 参数（D-2 学术诚信）\n"
            "   - VAE 预训练 loss 收敛验证（思路 2 验收）\n"
            "   - 几何约束消融实验（思路 3 验收）",
            file=sys.stderr,
        )
        return 0
    print(
        "[verify] 存在失败或仍被 skip 的用例 — 请排查上方 pytest 输出\n"
        "   常见原因：\n"
        "   - torch 版本不兼容（建议 torch>=2.0）\n"
        "   - 依赖包缺失（numpy/scipy 版本冲突）\n"
        "   - WinSock 仍损坏（run_pytest.py 绕过补丁未覆盖所有路径）",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
