"""
五轴刀路规划算法

支持三种五轴联动加工策略：
1. Lead-Angle (引导角控制) - 前倾角加工
2. Tilt-Angle (倾斜角控制) - 侧倾角加工
3. Interpolation (插值控制) - 刀轴矢量平滑过渡
"""

from dataclasses import dataclass
from enum import Enum
import math


class FiveAxisStrategy(Enum):
    """五轴加工策略"""

    LEAD_ANGLE = "lead_angle"  # 引导角控制
    TILT_ANGLE = "tilt_angle"  # 倾斜角控制
    INTERPOLATION = "interpolation"  # 插值控制


@dataclass
class ToolOrientation:
    """刀具姿态"""

    a_angle: float = 0.0  # A轴旋转角度 (度)
    c_angle: float = 0.0  # C轴旋转角度 (度)
    i_component: float = 0.0  # 刀轴矢量 I 分量
    j_component: float = 0.0  # 刀轴矢量 J 分量
    k_component: float = 1.0  # 刀轴矢量 K 分量

    def calculate_from_angles(self) -> None:
        """从A/C轴角度计算刀轴矢量"""
        a_rad = math.radians(self.a_angle)
        c_rad = math.radians(self.c_angle)

        self.i_component = math.sin(c_rad) * math.sin(a_rad)
        self.j_component = -math.cos(c_rad) * math.sin(a_rad)
        self.k_component = math.cos(a_rad)

    def calculate_angles_from_vector(self) -> None:
        """从刀轴矢量计算A/C轴角度"""
        # 计算A轴角度
        if abs(self.k_component) > 0.999:
            self.a_angle = 0.0
            self.c_angle = 0.0
        else:
            self.a_angle = math.degrees(math.acos(self.k_component))

            # 计算C轴角度
            if abs(self.i_component) < 1e-6 and abs(self.j_component) < 1e-6:
                self.c_angle = 0.0
            else:
                self.c_angle = math.degrees(math.atan2(self.i_component, -self.j_component))


@dataclass
class FiveAxisParams:
    """五轴加工参数"""

    strategy: FiveAxisStrategy = FiveAxisStrategy.LEAD_ANGLE
    lead_angle: float = 5.0  # 引导角 (度)
    tilt_angle: float = 0.0  # 倾斜角 (度)
    max_a_angle: float = 45.0  # A轴最大角度
    max_c_angle: float = 360.0  # C轴最大角度
    singularity_threshold: float = 5.0  # 奇异点阈值 (度)


class FiveAxisToolpathPlanner:
    """五轴刀路规划器"""

    def __init__(self, params: FiveAxisParams | None = None):
        self.params = params or FiveAxisParams()

    def plan_lead_angle_toolpath(
        self,
        start_x: float,
        start_y: float,
        start_z: float,
        end_x: float,
        end_y: float,
        end_z: float,
        surface_normal_i: float = 0.0,
        surface_normal_j: float = 0.0,
        surface_normal_k: float = 1.0,
        num_points: int = 5,
    ) -> list[ToolOrientation]:
        """
        引导角控制策略

        在刀具前进方向上保持固定的前倾角，适用于精加工曲面

        Args:
            start_x, start_y, start_z: 起点坐标
            end_x, end_y, end_z: 终点坐标
            surface_normal_i, surface_normal_j, surface_normal_k: 表面法向矢量
            num_points: 沿路径生成的姿态点数量

        Returns:
            刀具姿态列表（多个点）
        """
        orientations = []

        # 计算进给方向
        dx = end_x - start_x
        dy = end_y - start_y
        dz = end_z - start_z
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < 1e-6:
            return [ToolOrientation()]

        # 归一化进给方向
        feed_dir_i = dx / length
        feed_dir_j = dy / length
        feed_dir_k = dz / length

        # 计算引导角
        lead_rad = math.radians(self.params.lead_angle)

        # 沿路径生成多个刀具姿态点
        for i in range(num_points):
            # 刀轴矢量 = 表面法向旋转引导角
            # 简化计算：假设在XZ平面内倾斜
            tool_i = feed_dir_i * math.sin(lead_rad) + surface_normal_i * math.cos(lead_rad)
            tool_j = feed_dir_j * math.sin(lead_rad) + surface_normal_j * math.cos(lead_rad)
            tool_k = feed_dir_k * math.sin(lead_rad) + surface_normal_k * math.cos(lead_rad)

            # 归一化
            tool_length = math.sqrt(tool_i * tool_i + tool_j * tool_j + tool_k * tool_k)
            if tool_length > 1e-6:
                tool_i /= tool_length
                tool_j /= tool_length
                tool_k /= tool_length

            # 创建刀具姿态
            orientation = ToolOrientation(i_component=tool_i, j_component=tool_j, k_component=tool_k)
            orientation.calculate_angles_from_vector()

            # 检查奇异点
            if abs(orientation.a_angle) < self.params.singularity_threshold:
                orientation.a_angle = self.params.singularity_threshold
                orientation.calculate_from_angles()

            orientations.append(orientation)

        return orientations

    def plan_tilt_angle_toolpath(
        self,
        start_x: float,
        start_y: float,
        start_z: float,
        end_x: float,
        end_y: float,
        end_z: float,
        surface_normal_i: float = 0.0,
        surface_normal_j: float = 0.0,
        surface_normal_k: float = 1.0,
    ) -> list[ToolOrientation]:
        """
        倾斜角控制策略

        在刀具侧面保持固定倾斜角，适用于侧壁精加工

        Args:
            start_x, start_y, start_z: 起点坐标
            end_x, end_y, end_z: 终点坐标
            surface_normal_i, surface_normal_j, surface_normal_k: 表面法向矢量

        Returns:
            刀具姿态列表
        """
        orientations = []

        # 计算进给方向
        dx = end_x - start_x
        dy = end_y - start_y
        dz = end_z - start_z
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < 1e-6:
            return [ToolOrientation()]

        # 归一化进给方向
        feed_dir_i = dx / length
        feed_dir_j = dy / length
        feed_dir_k = dz / length

        # 计算侧倾方向 (垂直于进给方向和法向)
        # 叉积: tilt_dir = feed_dir × surface_normal
        tilt_dir_i = feed_dir_j * surface_normal_k - feed_dir_k * surface_normal_j
        tilt_dir_j = feed_dir_k * surface_normal_i - feed_dir_i * surface_normal_k
        tilt_dir_k = feed_dir_i * surface_normal_j - feed_dir_j * surface_normal_i

        # 归一化侧倾方向
        tilt_length = math.sqrt(tilt_dir_i * tilt_dir_i + tilt_dir_j * tilt_dir_j + tilt_dir_k * tilt_dir_k)
        if tilt_length > 1e-6:
            tilt_dir_i /= tilt_length
            tilt_dir_j /= tilt_length
            tilt_dir_k /= tilt_length

        # 计算倾斜角
        tilt_rad = math.radians(self.params.tilt_angle)

        # 刀轴矢量 = 表面法向旋转倾斜角
        tool_i = tilt_dir_i * math.sin(tilt_rad) + surface_normal_i * math.cos(tilt_rad)
        tool_j = tilt_dir_j * math.sin(tilt_rad) + surface_normal_j * math.cos(tilt_rad)
        tool_k = tilt_dir_k * math.sin(tilt_rad) + surface_normal_k * math.cos(tilt_rad)

        # 归一化
        tool_length = math.sqrt(tool_i * tool_i + tool_j * tool_j + tool_k * tool_k)
        if tool_length > 1e-6:
            tool_i /= tool_length
            tool_j /= tool_length
            tool_k /= tool_length

        # 创建刀具姿态
        orientation = ToolOrientation(i_component=tool_i, j_component=tool_j, k_component=tool_k)
        orientation.calculate_angles_from_vector()

        # 检查奇异点
        if abs(orientation.a_angle) < self.params.singularity_threshold:
            orientation.a_angle = self.params.singularity_threshold
            orientation.calculate_from_angles()

        orientations.append(orientation)
        return orientations

    def plan_interpolation_toolpath(
        self,
        points: list[tuple[float, float, float]],
        normals: list[tuple[float, float, float]],
    ) -> list[ToolOrientation]:
        """
        插值控制策略

        在多个点之间平滑过渡刀轴矢量，适用于复杂曲面精加工

        Args:
            points: 点位列表 [(x, y, z), ...]
            normals: 法向列表 [(i, j, k), ...]

        Returns:
            刀具姿态列表
        """
        if len(points) != len(normals):
            raise ValueError("点数和法向数必须相等")

        if len(points) < 2:
            return [ToolOrientation()]

        orientations = []

        for i in range(len(points) - 1):
            normal_start = normals[i]
            normal_end = normals[i + 1]

            # 线性插值刀轴矢量
            for t in [0.0, 0.5, 1.0]:
                # 插值法向
                interp_i = normal_start[0] * (1 - t) + normal_end[0] * t
                interp_j = normal_start[1] * (1 - t) + normal_end[1] * t
                interp_k = normal_start[2] * (1 - t) + normal_end[2] * t

                # 归一化
                length = math.sqrt(interp_i * interp_i + interp_j * interp_j + interp_k * interp_k)
                if length > 1e-6:
                    interp_i /= length
                    interp_j /= length
                    interp_k /= length

                # 创建刀具姿态
                orientation = ToolOrientation(i_component=interp_i, j_component=interp_j, k_component=interp_k)
                orientation.calculate_angles_from_vector()

                # 检查奇异点
                if abs(orientation.a_angle) < self.params.singularity_threshold:
                    orientation.a_angle = self.params.singularity_threshold
                    orientation.calculate_from_angles()

                orientations.append(orientation)

        return orientations

    def plan_toolpath(self, strategy: FiveAxisStrategy, **kwargs) -> list[ToolOrientation]:
        """
        根据策略规划刀路

        Args:
            strategy: 五轴加工策略
            **kwargs: 策略相关参数

        Returns:
            刀具姿态列表
        """
        if strategy == FiveAxisStrategy.LEAD_ANGLE:
            return self.plan_lead_angle_toolpath(
                start_x=kwargs.get("start_x", 0.0),
                start_y=kwargs.get("start_y", 0.0),
                start_z=kwargs.get("start_z", 0.0),
                end_x=kwargs.get("end_x", 10.0),
                end_y=kwargs.get("end_y", 0.0),
                end_z=kwargs.get("end_z", 0.0),
                surface_normal_i=kwargs.get("surface_normal_i", 0.0),
                surface_normal_j=kwargs.get("surface_normal_j", 0.0),
                surface_normal_k=kwargs.get("surface_normal_k", 1.0),
            )
        elif strategy == FiveAxisStrategy.TILT_ANGLE:
            return self.plan_tilt_angle_toolpath(
                start_x=kwargs.get("start_x", 0.0),
                start_y=kwargs.get("start_y", 0.0),
                start_z=kwargs.get("start_z", 0.0),
                end_x=kwargs.get("end_x", 10.0),
                end_y=kwargs.get("end_y", 0.0),
                end_z=kwargs.get("end_z", 0.0),
                surface_normal_i=kwargs.get("surface_normal_i", 0.0),
                surface_normal_j=kwargs.get("surface_normal_j", 0.0),
                surface_normal_k=kwargs.get("surface_normal_k", 1.0),
            )
        elif strategy == FiveAxisStrategy.INTERPOLATION:
            return self.plan_interpolation_toolpath(
                points=kwargs.get("points", [(0, 0, 0), (10, 0, 0)]),
                normals=kwargs.get("normals", [(0, 0, 1), (0, 0, 1)]),
            )
        else:
            raise ValueError(f"不支持的策略: {strategy}")
