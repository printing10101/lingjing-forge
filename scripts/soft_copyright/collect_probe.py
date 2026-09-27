#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""软著源码收集：探查自研代码文件清单与版权头情况（一次性分析脚本）。"""

import os
import re
import sys
from collections import Counter

ROOT = r"C:\Users\<user>\Desktop\灵境制造（上线版）"

# 自研代码目录（按逻辑顺序：后端 前端 桌面壳/rust）
INCLUDE_DIRS = [
    r"engineering\python\app",
    r"engineering\src",
    r"engineering\src-tauri",
    r"rust",
]
EXCLUDE_DIR_PARTS = (
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    ".git",
    "target",
    ".cargo",
    "coverage",
    ".vite",
)
EXCLUDE_FILE_PARTS = (".pyc", ".pyo", ".map", ".min.js")
# 测试/配置类文件不进入产品源码
EXCLUDE_DIR_NAMES = ("tests", "test", "__tests__", "migrations", "assets", "public", "static", "icons")
CODE_EXTS = {".py", ".ts", ".vue", ".rs", ".js", ".tsx", ".jsx", ".css", ".html"}

HEADER_KEYWORDS = re.compile(
    r"(?i)copyright|licensed under|apache license|spdx-license-identifier|"
    r"all rights reserved|gnu general public|mit license|bsd license"
)


def collect_files():
    files = []
    for base in INCLUDE_DIRS:
        base_path = os.path.join(ROOT, base)
        if not os.path.isdir(base_path):
            print(f"[skip] 目录不存在: {base_path}")
            continue
        for dirpath, dirnames, filenames in os.walk(base_path):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_PARTS and d not in EXCLUDE_DIR_NAMES]
            for fn in filenames:
                if fn.endswith(EXCLUDE_FILE_PARTS):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in CODE_EXTS:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                files.append((rel, full))
    return files


def probe_headers(files):
    """统计文件头部 30 行内命中版权关键词的文件。"""
    hits = []
    for rel, full in files:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                head = "".join([next(f, "") for _ in range(30)])
        except Exception as e:
            print(f"[read-error] {rel}: {e}")
            continue
        m = HEADER_KEYWORDS.search(head)
        if m:
            hits.append((rel, m.group(0)))
    return hits


def main():
    files = collect_files()
    print(f"总文件数: {len(files)}")
    by_dir = Counter(
        rel.split("/")[0] + ("/" + rel.split("/")[1] if len(rel.split("/")) > 1 else "") for rel, _ in files
    )
    for d, c in sorted(by_dir.items()):
        print(f"  {d}: {c} 文件")
    total_lines = 0
    for _, full in files:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                total_lines += sum(1 for _ in f)
        except Exception:
            pass
    print(f"总行数: {total_lines} 行（远超 60 页 x 50 行 = 3000 行需求）")

    hits = probe_headers(files)
    print(f"\n头部含版权关键词的文件数: {len(hits)}")
    for rel, kw in hits[:30]:
        print(f"  [{kw}] {rel}")


if __name__ == "__main__":
    sys.exit(main())
