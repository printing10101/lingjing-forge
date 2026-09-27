"""下载并转换 Zenodo i-CNC 铣削颤振数据集（record 15308467）→ 实测稳定性 schema。

i-CNC Use Case: Vibration data and associated Chatter indication during milling
process with CNC machines —— 真实 CNC 铣削振动数据 + 颤振标注，是当前可公开获取
的少数带"颤振标注"的铣削数据集之一（2025 年发布）。

用法（在本机执行，需联网）：
    cd research/experiments
    ../.venv/Scripts/python.exe real_validation/ingest_icnc_zenodo.py --dry-run   # 只查看文件清单
    ../.venv/Scripts/python.exe real_validation/ingest_icnc_zenodo.py             # 下载并尝试转换

诚实性约束：
    - 本脚本只做"格式转换"，不做任何数值生成。若原始数据列无法无歧义映射到
      schema（n_rpm/ap_mm/ae_mm/stable 等），脚本会停止并打印诊断，绝不编造。
    - 转换产物必须保留原始出处（source + doi），供审稿人核验。
"""

import argparse
import json
import os
import sys
import urllib.request
from typing import Dict, List

ZENODO_RECORD_ID = "15308467"
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_RECORD_ID}"

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "datasets", "icnc_chatter",
)
SCHEMA_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "datasets", "measured_stability", "measured_stability_points.csv",
)


def fetch_record(url: str) -> Dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binary(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "lingjing-research/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:
        f.write(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 Zenodo i-CNC 颤振数据集并尝试转换")
    ap.add_argument("--dry-run", action="store_true", help="只列出远程文件清单，不下载")
    ap.add_argument("--out-dir", default=OUT_DIR, help="原始数据保存目录")
    args = ap.parse_args()

    print(f"[1/3] 读取 Zenodo record {ZENODO_RECORD_ID} 元数据 ...")
    try:
        record = fetch_record(ZENODO_API)
    except Exception as e:  # noqa: BLE001
        print(f"  失败：无法访问 Zenodo API（{e}）")
        print("  若网络受限，请手动到 https://zenodo.org/records/15308467 下载后")
        print("  放入 datasets/icnc_chatter/，再运行 --dry-run 查看预期结构。")
        return 2

    files: List[Dict] = record.get("files", [])
    if not files:
        print("  记录中无文件，退出。")
        return 2

    print(f"  共 {len(files)} 个文件：")
    for f in files:
        print(f"    - {f['key']}  ({f.get('size', 0) / 1e6:.1f} MB)  [{f.get('type', '?')}]")

    if args.dry_run:
        print("\n[dry-run] 未下载。请检查上述文件清单，确认数据结构后去掉 --dry-run 执行。")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    print(f"[2/3] 下载到 {args.out_dir} ...")
    downloaded = []
    for f in files:
        key = f["key"]
        dest = os.path.join(args.out_dir, os.path.basename(key))
        try:
            print(f"    下载 {key} ...")
            fetch_binary(f["links"]["self"], dest)
            downloaded.append(dest)
        except Exception as e:  # noqa: BLE001
            print(f"    下载 {key} 失败：{e}")
    if not downloaded:
        print("  全部下载失败，退出。")
        return 2

    print(f"[3/3] 尝试转换为 schema ...（{SCHEMA_CSV}）")
    print("    注：Zenodo 原始格式可能随版本变化，转换规则需要人工核对。")
    print("    下一步：")
    print("      1. 用 pandas 查看下载文件的列名与颤振标注字段")
    print("      2. 若字段可无歧义映射（n/ap/ae/stable），补充转换逻辑后调用")
    print("         ingest_literature_points.py 的 --append 模式写入 schema CSV")
    print("      3. 每行保留 source='i-CNC Zenodo 15308467' 与对应 doi/url")
    return 1


if __name__ == "__main__":
    sys.exit(main())
