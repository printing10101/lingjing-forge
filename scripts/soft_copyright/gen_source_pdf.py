#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""软著源码文档生成器：抽取前 30 页 + 后 30 页源码，生成符合登记要求的 PDF。

规则（中国版权保护中心）：
- 每页 50 行（最后一页除外），页眉标注软件名称/版本/页码
- 前 30 页取程序起始部分，后 30 页取程序结尾部分
- 不含第三方代码与版权声明头（本仓库头部无版权声明，已探查确认）
"""

import os
import sys
from collections import Counter

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = r"C:\Users\<user>\Desktop\灵境制造（上线版）"
OUT_DIR = os.path.join(ROOT, "output", "软著材料")

SOFT_NAME = "灵境制造 AI-CAM 一体化软件"
SOFT_VERSION = "V1.0"

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
    ".idea",
    ".vs",
)
EXCLUDE_FILE_PARTS = (".pyc", ".pyo", ".map", ".min.js")
EXCLUDE_DIR_NAMES = ("tests", "test", "__tests__", "migrations", "assets", "public", "static", "icons")
CODE_EXTS = {".py", ".ts", ".vue", ".rs", ".js", ".tsx", ".jsx", ".css", ".html"}

LINES_PER_PAGE = 50
PAGES = 30  # 前 30 + 后 30
# 入口文件优先（程序起始部分从入口开始更符合直觉）
ENTRY_NAMES = ("main.py", "app.py", "main.ts", "index.ts", "lib.rs", "main.rs", "__init__.py")


def collect_files():
    files = []
    for base in INCLUDE_DIRS:
        base_path = os.path.join(ROOT, base)
        if not os.path.isdir(base_path):
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
    # 目录块顺序：后端 前端 桌面壳 rust；块内按路径排序；入口文件提到块首
    block_order = {d: i for i, d in enumerate(INCLUDE_DIRS)}

    def key(item):
        rel, _ = item
        base = rel.split("/")[0] + "/" + (rel.split("/")[1] if len(rel.split("/")) > 1 else "")
        block = next((b for b in INCLUDE_DIRS if rel.startswith(b)), "zzz")
        block_idx = block_order.get(block, 99)
        basename = os.path.basename(rel)
        entry_rank = 0 if basename in ENTRY_NAMES else 1
        return (block_idx, entry_rank, rel)

    files.sort(key=key)
    return files


def build_document_lines(files):
    """拼接全部源码：文件间插入分隔注释；返回行列表。"""
    lines = []
    for rel, full in files:
        sep = "// " + "=" * 40 + f" 文件: {rel} " + "=" * 40
        lines.append(sep)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.rstrip("\r\n").expandtabs(4)
                    # 剔除行尾空白，保留行本身；空行保留（空行也计入 50 行/页）
                    lines.append(line.rstrip())
        except Exception as e:
            print(f"[warn] 读取失败 {rel}: {e}")
    return lines


def register_fonts():
    simsun = r"C:\Windows\Fonts\simsun.ttc"
    pdfmetrics.registerFont(TTFont("SimSun", simsun, subfontIndex=0))
    return "SimSun"


def draw_page(c, page_no, page_lines, font_name, font_size, line_h, top_margin, usable_w):
    """绘制一页：页眉 + 50 行代码。返回 True 表示绘制成功。"""
    # 页眉
    c.setFont(font_name, 9)
    c.setFillColorRGB(0, 0, 0)
    header_text = f"{SOFT_NAME} {SOFT_VERSION}    第 {page_no} 页"
    c.drawString(2 * cm, A4[1] - 1.2 * cm, header_text)
    c.line(2 * cm, A4[1] - 1.4 * cm, A4[0] - 2 * cm, A4[1] - 1.4 * cm)

    y = top_margin
    c.setFont(font_name, font_size)
    for line in page_lines:
        if y < 1.5 * cm:
            break  # 防止越界（理论上不会发生，行高已算好）
        w = pdfmetrics.stringWidth(line, font_name, font_size)
        if w > usable_w:
            # 超宽行：先缩小字号，再超则截断
            reduced = max(6, font_size - 2)
            w2 = pdfmetrics.stringWidth(line, font_name, reduced)
            if w2 > usable_w:
                keep = int(len(line) * usable_w / w2) - 3
                line = line[: max(keep, 1)] + "…"
                w2 = pdfmetrics.stringWidth(line, font_name, reduced)
            c.setFont(font_name, reduced)
            c.drawString(2 * cm, y, line)
            c.setFont(font_name, font_size)
        else:
            c.drawString(2 * cm, y, line)
        y -= line_h


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = collect_files()
    print(f"收集文件: {len(files)}")

    by_block = Counter(
        rel.split("/")[0] + "/" + (rel.split("/")[1] if len(rel.split("/")) > 1 else "") for rel, _ in files
    )
    for d, c in sorted(by_block.items()):
        print(f"  {d}: {c}")

    lines = build_document_lines(files)
    print(f"拼接总行数: {len(lines)}")

    total = PAGES * LINES_PER_PAGE
    if len(lines) < total:
        print(f"[error] 源码总行数不足 {total} 行，无法生成 60 页文档")
        return 1
    head = lines[: PAGES * LINES_PER_PAGE]
    tail = lines[-PAGES * LINES_PER_PAGE :]
    all_pages = [head[i * LINES_PER_PAGE : (i + 1) * LINES_PER_PAGE] for i in range(PAGES)] + [
        tail[i * LINES_PER_PAGE : (i + 1) * LINES_PER_PAGE] for i in range(PAGES)
    ]
    assert len(all_pages) == 60

    font_name = register_fonts()
    font_size = 9
    usable_w = A4[0] - 4 * cm
    top_margin = A4[1] - 1.7 * cm
    line_h = (top_margin - 1.5 * cm) / LINES_PER_PAGE  # 可用高度 / 50 行

    out_pdf = os.path.join(OUT_DIR, f"{SOFT_NAME} 源代码（前30页+后30页）.pdf")
    c = canvas.Canvas(out_pdf, pagesize=A4)
    for i, page_lines in enumerate(all_pages, start=1):
        draw_page(c, i, page_lines, font_name, font_size, line_h, top_margin, usable_w)
        c.showPage()
    c.save()
    print(f"\n生成成功: {out_pdf}")
    print(f"共 {len(all_pages)} 页，每页 {LINES_PER_PAGE} 行（最后一页按实际行数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
