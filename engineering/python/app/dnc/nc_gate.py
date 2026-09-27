"""DNC 下发硬闸：NC 程序必须通过阶段 7 校验（含体素仿真）才能发送到机床。

闭环语义（优化升级路线图 A 线「仿真强制闭环」）：
    AI 生成的 NC 代码必须通过体素仿真校验才能导出/下发。本模块是
    「下发」侧的强制点：``POST /api/v1/dnc/nc-program/send`` 在发送前
    调用 :func:`get_dispatch_block_reason`，要求目标程序能追溯到一个
    满足以下全部条件的阶段 7 校验任务：

    1. ``source_gcode_file_path`` 与待发送程序是同一个文件（路径归一化
       后比对，Windows 下忽略大小写）；
    2. 任务状态为 SUCCEEDED（工程师已完成审核确认）；
    3. ``voxel_check_passed is True``（体素材料去除仿真通过；
       None = 闭环上线前的历史任务/未执行，按未通过处理）。

    「导出」侧不设闸：阶段 6 的导出产物是阶段 7 校验的输入
    （confirm 写 report.json → 阶段 7 读取），在导出处拦截会造成流水线死锁；
    阶段 6 的响应与 disclaimer 已声明产物仅供校验参考。

显式逃生阀：
    ``LNN_DNC_ALLOW_UNVALIDATED_NC=1`` 允许下发未追溯的程序（用于
    历史手写程序/外部程序传输）。开启时每次下发都会打 warning 日志，
    并写入哈希链审计日志（``app.audit``，防篡改、可追溯）；审计写入
    失败时放行同样失败——无留痕的未校验下发本身就是安全事件。
    默认关闭（fail-closed）。

工程优先策略（项目记忆硬约束）：
    系统绝不主动替工程师做「可上机」的最终判断——闸门放行只表示
    「程序已通过本系统能做的全部软件校验」，物理机床执行仍由
    持证操作员完成。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 显式逃生阀环境变量（fail-closed：默认未通过）
ALLOW_UNVALIDATED_ENV: str = "LNN_DNC_ALLOW_UNVALIDATED_NC"


def _norm_path(path: str) -> str:
    """路径归一化：绝对化 + Windows 大小写归一，用于追溯比对。"""
    return os.path.normcase(os.path.abspath(path))


def _allow_unvalidated_enabled() -> bool:
    return os.environ.get(ALLOW_UNVALIDATED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _audit_unvalidated_dispatch(program_path: str, machine_id: str | None) -> None:
    """逃生阀放行必须留下哈希链审计记录，写不进审计就放不了行。

    与 auth 审计「失败不阻断业务」刻意相反：这里是安全闸门的
    fail-open 路径，审计链断裂时继续下发等于无留痕放行未校验程序，
    因此向上抛 RuntimeError 让下发端 fail-closed。
    """
    from app.audit.audit_log import get_audit_log
    from app.audit.chain import OperationStatus

    get_audit_log().log_security_event(
        event_type="dnc_unvalidated_nc_dispatch",
        operation_status=OperationStatus.SUCCESS,
        input_parameters={"program_path": program_path},
        metadata={
            "machine_id": machine_id,
            "escape_hatch_env": ALLOW_UNVALIDATED_ENV,
        },
    )


def get_dispatch_block_reason(program_path: str, machine_id: str | None = None) -> str | None:
    """判断 NC 程序是否允许下发到机床。

    Args:
        program_path: 待发送的本地 NC 程序路径。
        machine_id: 目标机床标识，仅用于逃生阀放行时的审计留痕。

    Returns:
        None 表示允许下发；否则返回拒绝原因文本（面向操作员，
        含可直接执行的补救动作）。

    Raises:
        RuntimeError: 逃生阀开启但审计日志写入失败（fail-closed）。
    """
    if _allow_unvalidated_enabled():
        logger.warning(
            "DNC 下发闸门已由 %s=1 显式关闭：程序 %s 未经过阶段 7 校验追溯即下发"
            "（历史/外部程序模式，请确保人工完成校验）",
            ALLOW_UNVALIDATED_ENV,
            program_path,
        )
        _audit_unvalidated_dispatch(program_path, machine_id)
        return None

    # 延迟导入：避免 dnc 模块加载即拉起 cam_validation → simulation 依赖链
    from app.cam_validation.cam_store import (
        CamTaskStore,
        CamValidationTaskStatus,
    )

    target = _norm_path(program_path)
    store = CamTaskStore()
    matches = [
        t for t in store.list_tasks() if t.source_gcode_file_path and _norm_path(t.source_gcode_file_path) == target
    ]

    if not matches:
        return (
            "未找到该 NC 程序的阶段 7 校验记录，DNC 下发被仿真强制闭环拦截。"
            "请先在阶段 7 对该程序所属任务执行 CAM 校验（含体素仿真），"
            "或确认发送的是任务导出的原始 G 代码文件路径"
            f"（如确需传输外部/历史程序，可设置 {ALLOW_UNVALIDATED_ENV}=1 并自行承担校验责任）。"
        )

    succeeded = [t for t in matches if t.status == CamValidationTaskStatus.SUCCEEDED.value]
    if not succeeded:
        statuses = ", ".join(sorted({t.status for t in matches}))
        return (
            f"该 NC 程序的阶段 7 校验任务尚未完成（当前状态：{statuses}），"
            "DNC 下发被仿真强制闭环拦截。请完成校验与工程师审核确认（SUCCEEDED）后再下发。"
        )

    # 多条命中时取最近完成的一条（completed_at 最大）
    latest = max(succeeded, key=lambda t: t.completed_at)
    if latest.voxel_check_passed is None:
        return (
            "该 NC 程序的校验任务产生于体素仿真闭环上线前（voxel_check_passed 未知），"
            "DNC 下发被仿真强制闭环拦截。请对阶段 6 任务重新执行阶段 7 校验后再下发。"
        )
    if latest.voxel_check_passed is False:
        return (
            f"该 NC 程序的体素材料去除仿真未通过（检测到 {latest.voxel_collision_count} 处碰撞），"
            "DNC 下发被仿真强制闭环拦截。请回阶段 6 修改刀轨/参数后重新生成并校验。"
        )

    # 程序级运动学校验（行程/主轴/进给/快移扎刀/装刀）
    if latest.kinematics_check_passed is None:
        return (
            "该 NC 程序的校验任务产生于运动学校验闭环上线前（kinematics_check_passed 未知），"
            "DNC 下发被仿真强制闭环拦截。请对阶段 6 任务重新执行阶段 7 校验后再下发。"
        )
    if latest.kinematics_check_passed is False:
        return (
            f"该 NC 程序的程序级运动学校验未通过（{latest.kinematics_error_count} 处 error："
            "行程/主轴/进给/快移扎刀/装刀），"
            "DNC 下发被仿真强制闭环拦截。请修正程序后重新执行阶段 7 校验。"
        )

    return None
