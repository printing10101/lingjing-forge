"""三层架构枚举定义（从 interfaces 拆出）。"""

from __future__ import annotations

from enum import Enum


class LayerType(str, Enum):
    COGNITIVE = "cognitive"
    PERCEPTION = "perception"
    EXECUTION = "execution"


class ProcessCategory(str, Enum):
    TURNING = "turning"
    MILLING = "milling"
    DRILLING = "drilling"
    GRINDING = "grinding"
    BORING = "boring"
    EDM = "edm"
    ADDITIVE = "additive"
    HYBRID = "hybrid"


class QualityLevel(str, Enum):
    IT5 = "IT5"
    IT6 = "IT6"
    IT7 = "IT7"
    IT8 = "IT8"
    IT9 = "IT9"
    IT10 = "IT10"
    IT11 = "IT11"
    IT12 = "IT12"


class SurfaceFinishGrade(str, Enum):
    N1 = "N1"
    N2 = "N2"
    N3 = "N3"
    N4 = "N4"
    N5 = "N5"
    N6 = "N6"
    N7 = "N7"
    N8 = "N8"
    N9 = "N9"
    N10 = "N10"
    N11 = "N11"
    N12 = "N12"


class SensorType(str, Enum):
    ACCELEROMETER = "accelerometer"
    THERMOCOUPLE = "thermocouple"
    DYNAMOMETER = "dynamometer"
    ACOUSTIC_EMISSION = "acoustic_emission"
    LASER_DISPLACEMENT = "laser_displacement"
    VISION_CAMERA = "vision_camera"
    CURRENT_PROBE = "current_probe"
    PRESSURE_SENSOR = "pressure_sensor"


class FeatureExtractionAlgorithm(str, Enum):
    VGG16 = "vgg16"
    RESNET50 = "resnet50"
    EFFICIENTNET = "efficientnet"
    VIT = "vit"
    POINTNET = "pointnet"
    DGCNN = "dgcnn"
    FPN = "fpn"
    CUSTOM_CNN = "custom_cnn"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class EventType(str, Enum):
    TOOL_WEAR_THRESHOLD = "tool_wear_threshold"
    VIBRATION_ANOMALY = "vibration_anomaly"
    TEMPERATURE_ANOMALY = "temperature_anomaly"
    COLLISION_DETECTED = "collision_detected"
    SURFACE_QUALITY_DEGRADATION = "surface_quality_degradation"
    SPINDLE_OVERLOAD = "spindle_overload"
    COOLANT_FAILURE = "coolant_failure"
    DIMENSIONAL_DEVIATION = "dimensional_deviation"


class AdjustmentPriority(str, Enum):
    IMMEDIATE = "immediate"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
