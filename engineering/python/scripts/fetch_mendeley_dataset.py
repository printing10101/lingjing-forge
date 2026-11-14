"""从 Mendeley Data 拉取公开数据集（逐文件直链 + SHA256 校验）.

背景：Zenodo 直连在本机不可达（域名解析失败），Mendeley Data 为同一
数据集的官方镜像（Data in Brief 2024, JUST_CAS_milling, CC BY 4.0）。
本脚本只做下载与校验，不改动数据内容。

行为
----
- 读取 ``<base>/public-api/datasets/<id>`` 的文件清单（含 sha256）；
- 已存在且哈希一致的文跳过；不一致的重新下载；
- 单文件失败重试 3 次，整体失败不删除已下载文件（可续跑）；
- 结束时输出清单核对结果（数量、总字节、哈希通过数）。

用法::

    python engineering/python/scripts/fetch_mendeley_dataset.py \
        --dataset 8jk3kyxmbn --out data/external/just_cas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://data.mendeley.com/public-api/datasets/{dataset}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; dataset-fetch/1.0)"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, target: Path, expected_sha: str, *, retries: int = 3) -> str:
    """下载单文件；返回状态 downloaded/skipped。"""
    if target.exists():
        if sha256_of(target) == expected_sha:
            return "skipped"
        target.unlink()
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as response:
                data = response.read()
            target.write_bytes(data)
            if sha256_of(target) != expected_sha:
                raise ValueError(f"哈希不匹配: {target.name}")
            return "downloaded"
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if attempt == retries:
                raise RuntimeError(f"下载失败 {target.name}: {exc}") from exc
            time.sleep(3 * attempt)
    return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="8jk3kyxmbn")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(API.format(dataset=args.dataset), headers=UA), timeout=60) as response:
        meta = json.loads(response.read().decode("utf-8"))
    files = meta.get("files") or []
    if not files:
        raise SystemExit("数据集无文件清单")
    print(f"[fetch] {meta.get('name')} v{meta.get('version')} files={len(files)}", flush=True)
    counts = {"downloaded": 0, "skipped": 0}
    total_bytes = 0
    started = time.perf_counter()
    for index, entry in enumerate(files, 1):
        details = entry["content_details"]
        target = args.out / entry["filename"]
        status = fetch(details["download_url"], target, details["sha256_hash"])
        counts[status] += 1
        total_bytes += details["size"]
        if index % 10 == 0 or index == len(files):
            elapsed = time.perf_counter() - started
            print(f"[fetch] {index}/{len(files)} {elapsed:.0f}s "
                  f"({total_bytes / 1e6:.0f}MB planned)", flush=True)
    manifest = args.out / "manifest.json"
    manifest.write_text(json.dumps({"dataset": args.dataset, "name": meta.get("name"),
                                    "version": meta.get("version"),
                                    "licence": (meta.get("data_licence") or {}).get("name"),
                                    "files": [{"filename": entry["filename"],
                                               "sha256": entry["content_details"]["sha256_hash"],
                                               "size": entry["content_details"]["size"]} for entry in files]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch] done: downloaded={counts['downloaded']} skipped={counts['skipped']} "
          f"bytes={total_bytes} manifest={manifest}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
