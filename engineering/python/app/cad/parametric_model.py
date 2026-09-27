"""NL2CAD 参数化直调（借鉴 CADAM/nurb 的滑杆式参数编辑）。

LLM 生成的 CadQuery 脚本里的尺寸（长/宽/高/半径…）被提取为命名参数暴露给
前端；用户拖动滑杆改尺寸时，后端**只重执行脚本，不重新调 LLM**——
改一个孔位从"一次推理 + 数秒等待"变成"零推理 + 亚秒级"。

实现（AST 级，非正则）：
- ``extract_parameters``：收集脚本顶层「变量名 = 数字字面量」赋值为参数表；
- ``apply_parameters``：对参数表做白名单覆写（键必须已存在、值必须是
  有限正数），AST 常量节点原位替换后 ``ast.unparse`` 重建脚本——
  覆写值只进数字常量节点，代码结构不可能被注入改写；
- ``regenerate_model``：应用覆写 → AST 安全审计 → 沙箱执行导出 →
  B-rep 校验（execute_and_export 内置），与 NL2CAD 生成路径同一套门禁。

配套约定（nl2cad_llm 系统提示词已同步）：LLM 被要求把关键尺寸先赋值给
命名变量再建模；脚本若直接内联字面量（``.box(50, 30, 20)``），参数表
为空属预期行为——调用方此时不展示滑杆（诚实降级，不造假参数）。
"""

from __future__ import annotations

import ast
import logging
import math
import uuid
from dataclasses import dataclass, field

from app.cad.cadquery_gen import CadQueryGenerator, _CadQueryScriptValidator

logger = logging.getLogger(__name__)


class ParameterError(Exception):
    """参数化直调失败（非法键 / 非法值 / 脚本不可参数化）。"""


@dataclass
class ParametricScript:
    """可参数化的 CadQuery 脚本。"""

    script: str
    parameters: dict[str, float] = field(default_factory=dict)

    @property
    def is_parametric(self) -> bool:
        """脚本是否含可调参数（内联字面量脚本为 False，前端不展示滑杆）。"""
        return bool(self.parameters)


def extract_parameters(script: str) -> dict[str, float]:
    """提取脚本顶层的数字赋值为参数表（name → value）。

    只认「单目标 Name = 数字常量」（含负数与科学计数法），忽略函数内
    赋值、复合赋值（+=）、元组解包与非数值常量——这些不是安全的滑杆语义。

    已知局限：顶层「非尺寸」数字赋值（如齿数 ``teeth = 12``、行数
    ``rows = 3``）也会进参数表，前端按 mm 尺寸渲染 ±50% 滑杆。此处不做
    语义猜测（无法可靠区分计数与尺寸），靠生成侧提示词约定"只对关键
    尺寸命名变量"约束。
    """
    tree = _parse_script(script)
    parameters: dict[str, float] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
        ):
            num = float(value.value)
            if math.isfinite(num):
                parameters[target.id] = num
    return parameters


def apply_parameters(script: str, overrides: dict[str, float]) -> str:
    """把参数覆写应用到脚本，返回新脚本文本。

    白名单约束：
    - 键必须已存在于参数表（不允许新增变量名 → 无法注入新代码语义）；
    - 值必须是有限正数（与建模提示词的尺寸契约一致，0/负数/NaN 拒绝）。

    Raises:
        ParameterError: 脚本语法错误 / 键不存在 / 值非法。
    """
    if not overrides:
        return script

    tree = _parse_script(script)
    known = extract_parameters(script)

    for name, value in overrides.items():
        if name not in known:
            raise ParameterError(
                f"未知参数 {name!r}：脚本可调参数为 {sorted(known) or '（无）'}。"
                f"只允许覆写已存在的参数，不允许新增变量。"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(f"参数 {name} 的值 {value!r} 必须是数字")
        num = float(value)
        if not math.isfinite(num) or num <= 0:
            raise ParameterError(f"参数 {name} 的值 {value!r} 必须是有限正数（mm）")

    replaced: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in overrides:
            continue
        value = overrides[target.id]
        node.value = ast.Constant(value=float(value))
        replaced.add(target.id)

    new_script = ast.unparse(tree)
    logger.info("参数化覆写完成: %s", sorted(replaced))
    return new_script


async def regenerate_model(
    script: str,
    overrides: dict[str, float],
    task_id: str | None = None,
    output_format: str = "step",
) -> tuple[str, dict[str, float]]:
    """应用参数覆写并重新执行脚本（无 LLM 参与），返回 (模型路径, 新参数表)。

    与生成路径同一套门禁：AST 安全审计 → 沙箱执行 + 导出 → B-rep 校验。

    Raises:
        ParameterError: 覆写参数非法。
        CadQueryScriptError / CadQueryError: 脚本审计或执行失败。
    """
    new_script = apply_parameters(script, overrides)

    # 与 nl2cad_llm 同源的 AST 审计（拒绝 import / 危险 dunder）
    _CadQueryScriptValidator().visit(_parse_script(new_script))

    generator = CadQueryGenerator()
    tid = task_id or f"nl2cad_regen_{uuid.uuid4().hex[:8]}"
    output_path = await generator.execute_and_export(new_script, tid, output_format)

    new_params = extract_parameters(new_script)
    logger.info("参数化重执行成功: %s（%d 个参数）", output_path, len(new_params))
    return output_path, new_params


def _parse_script(script: str) -> ast.Module:
    """解析脚本来 AST；语法错误统一为 ParameterError（带原始错误信息）。"""
    try:
        return ast.parse(script)
    except SyntaxError as e:
        raise ParameterError(f"脚本语法错误，无法参数化: {e}") from e


def script_to_parametric(script: str) -> ParametricScript:
    """便捷封装：脚本 + 参数表一次提取。"""
    return ParametricScript(script=script, parameters=extract_parameters(script))


__all__ = [
    "ParameterError",
    "ParametricScript",
    "extract_parameters",
    "apply_parameters",
    "regenerate_model",
    "script_to_parametric",
]
