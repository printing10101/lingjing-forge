"""正交切削 FEA 结果分析：提取刀具反力 → 稳态切削力 → 与 Kienzle 预测对比。

用法: python analyze_cut.py <jobname>
"""
import re
import sys
from pathlib import Path

def parse_rf(dat_path):
    """解析 .dat 中 'forces (fx,fy,fz) for set TOOLTOP and time X' 帧 → [(t, Fx, Fy), ...]"""
    text = Path(dat_path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    frames = []
    cur_time = None
    fx = fy = fz = 0.0
    for ln in lines:
        m = re.search(r"forces \(fx,fy,fz\) for set \S+ and time\s+([\d.E+-]+)", ln)
        if m:
            if cur_time is not None:
                frames.append((cur_time, fx, fy, fz))
            cur_time = float(m.group(1))
            fx = fy = fz = 0.0
            continue
        if cur_time is None:
            continue
        m2 = re.match(r"\s*(\d+)\s+([\d.E+-]+)\s+([\d.E+-]+)\s+([\d.E+-]+)", ln)
        if m2:
            fx += float(m2.group(2))
            fy += float(m2.group(3))
            fz += float(m2.group(4))
    if cur_time is not None:
        frames.append((cur_time, fx, fy, fz))
    return frames

def kienzle_prediction(kc11=2000.0, mc=0.25, b=1.0, h=0.1):
    fz = kc11 * b * (h ** (1 - mc))
    return {"Fz": fz, "Fx": 0.3 * fz, "Fy": 0.4 * fz}

def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "orthogonal_cut"
    dat = Path(job + ".dat")
    if not dat.exists():
        print(f"[错误] 找不到 {dat}。")
        return
    frames = parse_rf(dat)
    if not frames:
        print("未解析到反力帧")
        return
    print(f"解析到 {len(frames)} 帧反力，时间跨度 {frames[0][0]:.2e} ~ {frames[-1][0]:.2e} s")
    # 前 10% 为切入瞬态，取后 60% 为稳态
    n = len(frames)
    steady = frames[int(n * 0.4):]
    if not steady:
        steady = frames
    avg_fx = sum(f[1] for f in steady) / len(steady)
    avg_fy = sum(f[2] for f in steady) / len(steady)
    max_fx = max(f[1] for f in frames)
    max_fy = max(f[2] for f in frames)
    print(f"稳态平均反力: Fx(切削方向)={avg_fx:8.2f} N, Fy(切深方向)={avg_fy:8.2f} N")
    print(f"峰值反力:     Fx={max_fx:8.2f} N, Fy={max_fy:8.2f} N")
    pred = kienzle_prediction()
    print(f"\nKienzle 预测 (45钢, b=1mm, h=0.1mm):")
    print(f"  Fz(主切削力) = {pred['Fz']:8.2f} N   <- FEA Fx 对应")
    print(f"  Fx(进给力)   = {pred['Fx']:8.2f} N   <- FEA Fy 对应")
    print(f"  Fy(径向力)   = {pred['Fy']:8.2f} N")
    print(f"\n对比 (FEA/Kienzle):")
    print(f"  主切削力: FEA {avg_fx:.1f} vs Kienzle {pred['Fz']:.1f} -> 比值 {avg_fx/pred['Fz']:.2f}")
    print(f"  进给力:   FEA {avg_fy:.1f} vs Kienzle {pred['Fx']:.1f} -> 比值 {avg_fy/pred['Fx']:.2f}")

if __name__ == "__main__":
    main()
