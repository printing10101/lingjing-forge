"""CalculiX .frd 结果解析器（通用，处理定宽+无分隔混合格式）。

用法: python analyze_frd.py <jobname>.frd [块名]
块名: DISP / STRESS / FORC 等（默认解析所有）
"""
import re
import sys
from pathlib import Path

TOKEN = re.compile(r'-?\d+\.?\d*(?:E[+-]?\d+)?')


def parse_frd_block(lines, key: str) -> dict:
    """提取 frd 中 key 块（如 '-4  DISP'）的节点数据 {node_id: [values]}。"""
    data = {}
    active = False
    for ln in lines:
        s = ln.strip()
        if s.startswith(key):
            active = True
            continue
        if active:
            if s.startswith('-3'):
                break
            t = TOKEN.findall(s)
            if len(t) >= 3:  # t[0]='-1' 行标识, t[1]=节点号, 其余=分量
                data[int(t[1])] = [float(x) for x in t[2:]]
    return data


def von_mises(s) -> float:
    s11, s22, s33, s12 = s[0], s[1], s[2], s[3]
    return (0.5 * ((s11 - s22) ** 2 + (s22 - s33) ** 2 + (s33 - s11) ** 2) + 6 * s12 ** 2) ** 0.5


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "equivalent_load.frd"
    only = sys.argv[2].upper() if len(sys.argv) > 2 else None
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()

    blocks = {"DISP": "-4  DISP", "STRESS": "-4  STRESS", "FORC": "-4  FORC"}
    for name, key in blocks.items():
        if only and name != only:
            continue
        data = parse_frd_block(lines, key)
        if not data:
            continue
        print(f"=== {name} ({len(data)} 节点) ===")
        if name == "DISP":
            mx_id = max(data, key=lambda k: (data[k][0] ** 2 + data[k][1] ** 2) ** 0.5)
            mx = (data[mx_id][0] ** 2 + data[mx_id][1] ** 2) ** 0.5
            print(f"最大合位移: {mx:.5f} mm @节点{mx_id}")
        elif name == "STRESS":
            smax_id = max(data, key=lambda k: von_mises(data[k]))
            print(f"最大 von Mises: {von_mises(data[smax_id]):.0f} MPa @节点{smax_id}")
