"""跨方言「起始段 Z」一致性回归（2026-10-07 发现的 P0 输出缺陷）。

病根：`format_tool_change()` 在 `G43 Z{safe_z_height} H{tool}` 之后紧跟一条
下移指令。`fanuc_0i` 用的是安全平面高度，但 gsk / hnc / knd / mitsubishi /
fagor / xmachine 六个方言写的是 **刀具长度补偿值** `length_comp`：

    G43 Z80.000 H01
    G01 Z1.000 F10000.000      ← length_comp，不是 Z 坐标

`length_comp` 是 H 号对应的偏置量（本仓实跑里 = 1.0，黄金测试里 = 30/50），
把它当 Z 坐标输出，等于每个程序的第一个进给动作就把刀尖命令到 Z≈1mm——
在机床上就是撞台面/夹具。而阶段 7 的体素校验只查「过切底面」和「快移扎料」，
**G01 进给段穿过未加工材料不在检查范围**，所以这条危险指令能过闸。

本测试把所有已注册方言拉齐到同一条不变量上：
换刀块里的首个 `G01/G00 Z` 必须等于该方言的 `safe_z_height`，
且不得等于传入的 `length_comp`。
"""

from __future__ import annotations

import re

import pytest

from app.postprocessor.registry import PostProcessorRegistry

# 明显不等于任何安全高度的偏置值：用它当探针，能区分「安全平面」与「长度补偿」
_PROBE_LENGTH_COMP = 7.77
_PROBE_RADIUS_COMP = 0.0

# 各方言的 Z 运动写法都得认：Fanuc 族 `G00/G01 Z80.000`、Siemens 的 N 段与
# 模态裸 Z、Heidenhain 的 `L  Z+80.000`。只认 `^G0?1 Z` 会把正确输出误判成缺失。
_MOTION_KW = re.compile(r"\b(G00|G01|RL|L)\b")
_Z_WORD = re.compile(r"\bZ([-+]?[\d.]+)")
# 回零/参考点返回行（`G00 G91 G28 Z0.`、HNC 的 `G00 G91 G74 Z0.`）里的 Z0.
# 是参考点标志位，不是刀具位置；同理 G53 是机床坐标系选择。
_HOMING_KW = re.compile(r"\b(G28|G30|G74|G53)\b")
_TOOL_CHANGE_KW = re.compile(r"(M06|TOOL CALL|T=\")", re.I)


def _first_z_move(text: str) -> float | None:
    """返回换刀并建立长度补偿**之后**第一条 Z 运动的 Z 值。

    起点选择很关键，否则探针会从别的行读出 Z 值而失去判别力：
    - 回零行（`G00 G91 G28 Z0.` / HNC `G00 G91 G74 Z0.`）里的 Z0. 是参考点标志位；
    - 补偿建立行（`G00 G43 Z80.000 H01`）的 Z 是安全接近高度，不是本段的移动目标
      ——若把它当答案，`G01 Z{length_comp}` 这个 bug 就会被遮住（变异验证实测过）。
    所以：先跳过到换刀行（M06 / TOOL CALL / T=），再跳过补偿建立行，
    取之后第一条带 Z 地址的运动行。
    """
    lines = text.splitlines()
    start = 0
    for i, raw in enumerate(lines):
        if _TOOL_CHANGE_KW.search(raw):
            start = i + 1
            break
    # 补偿建立行之后才是"起手动作"
    for i in range(start, len(lines)):
        if re.search(r"\bG4[1239]\b", lines[i]):
            start = i + 1

    for raw in lines[start:]:
        line = raw.strip()
        if not line or _Z_WORD.search(line) is None:
            continue
        if _HOMING_KW.search(line):
            continue
        if not _MOTION_KW.search(line) and not re.match(r"^(N\d+\s+)?Z[-+]?\d", line):
            continue
        m = _Z_WORD.search(line)
        if m:
            return float(m.group(1))
    return None


def _dialect_ids() -> list[str]:
    return PostProcessorRegistry().list_controllers()


class TestToolChangeStartMove:
    @pytest.mark.regression
    @pytest.mark.parametrize("controller_id", _dialect_ids())
    def test_first_feed_move_is_safe_height_not_length_comp(self, controller_id: str):
        pp = PostProcessorRegistry().get_processor(controller_id)
        if not hasattr(pp, "format_tool_change"):
            pytest.skip(f"{controller_id} 不提供 format_tool_change")

        text = pp.format_tool_change(tool_id=2, length_comp=_PROBE_LENGTH_COMP, radius_comp=_PROBE_RADIUS_COMP)
        first_z = _first_z_move(text)
        assert first_z is not None, f"{controller_id} 换刀块里找不到 Z 移动指令：{text!r}"

        safe_z = float(pp.safe_z_height)
        assert first_z == pytest.approx(safe_z), (
            f"{controller_id} 换刀后首个 Z 移动 = Z{first_z}，应为安全平面 Z{safe_z}。"
            f"若等于 length_comp({_PROBE_LENGTH_COMP}) 说明把刀具长度补偿当成了 Z 坐标（撞刀风险）。"
        )
        if abs(safe_z - _PROBE_LENGTH_COMP) > 1e-6:
            assert abs(first_z - _PROBE_LENGTH_COMP) > 1e-6, f"{controller_id} 把 length_comp 当成了 Z 坐标"

    @pytest.mark.regression
    @pytest.mark.parametrize("controller_id", _dialect_ids())
    def test_safe_height_is_above_work_surface(self, controller_id: str):
        """安全平面本身必须可检查：方言若把 safe_z 配成 0 会让上面那条断言失去意义。"""
        pp = PostProcessorRegistry().get_processor(controller_id)
        assert float(pp.safe_z_height) > 0, f"{controller_id} safe_z_height 非正，起始段无从判断"
