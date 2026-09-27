"""DXF模块异常定义。

定义DXF文件处理全流程中可能出现的异常类型，
提供分层错误分类，支持上游模块进行精确的错误处理。
"""

from __future__ import annotations


class DxfError(Exception):
    """DXF模块基础异常类。

    所有DXF相关异常的基类，支持统一的异常捕获和处理。
    """


class DxfParseError(DxfError):
    """DXF文件解析异常。

    在DXF文件读取、格式识别、实体提取过程中发生错误时抛出。
    可能原因：文件不存在、格式损坏、版本不兼容、实体提取失败。
    """


class DxfFormatError(DxfParseError):
    """DXF格式错误。

    文件不是有效的DXF格式或版本不受支持时抛出。
    """


class DxfFeatureError(DxfError):
    """特征提取异常。

    在从几何数据中提取加工特征过程中发生错误时抛出。
    可能原因：几何数据为空、特征识别算法失败、参数推断异常。
    """


class DxfModelError(DxfError):
    """模型转换异常。

    在2D DXF数据向3D CadQuery模型转换过程中发生错误时抛出。
    可能原因：CadQuery操作失败、布尔运算异常、几何参数无效。
    """


class DxfPipelineError(DxfError):
    """DXF流水线异常。

    在DXF处理流水线执行过程中发生错误时抛出。
    """
