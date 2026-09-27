"""运动学仿真模块。

当前能力：3 轴程序级运动学校验（行程/主轴/进给/快移扎刀/装刀/程序完整性），
由 :class:`KinematicsValidator` 提供；不做几何碰撞（VoxelValidator/CollisionDetector
职责）与多轴 RTCP 逆解。
"""

from app.simulation.kinematics.interpreter import (
    GCodeKinematicsInterpreter,
    MotionRecord,
    ProgramTrace,
)
from app.simulation.kinematics.machine import (
    AxisLimits,
    MachineProfile,
    auto_work_offset,
    load_profile,
    profile_from_machine_dict,
)
from app.simulation.kinematics.validator import (
    KinematicsIssue,
    KinematicsReport,
    KinematicsValidator,
)

__all__ = [
    "AxisLimits",
    "GCodeKinematicsInterpreter",
    "KinematicsIssue",
    "KinematicsReport",
    "KinematicsValidator",
    "MachineProfile",
    "MotionRecord",
    "ProgramTrace",
    "auto_work_offset",
    "load_profile",
    "profile_from_machine_dict",
]
