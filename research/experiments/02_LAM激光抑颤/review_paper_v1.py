# -*- coding: utf-8 -*-
"""论文三采样评审脚本（ollama-paper-review 模板，GPU 模式）
评审对象：桌面 LAM激光主动抑颤论文_初稿v5.docx
评审栈：主实例 11434 + qwen3:14b-128k（GPU 空闲窗口）
"""
import json, urllib.request, pathlib, re, statistics
import docx as docxlib
from docx.table import Table
from docx.text.paragraph import Paragraph

DOCX_PATH = r"C:\Users\<user>\Desktop\LAM激光主动抑颤论文_初稿v5.docx"
OUT_PREFIX = r"C:\Users\<user>\Desktop\灵境制造（上线版）\docs\review_outputs\review_v5_run"
URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:14b-128k"
TEMPERATURE = 0.4

pathlib.Path(r"C:\Users\<user>\Desktop\灵境制造（上线版）\docs\review_outputs").mkdir(exist_ok=True)

d = docxlib.Document(DOCX_PATH)
parts = []
for child in d.element.body.iterchildren():
    if child.tag.endswith("}p"):
        t = Paragraph(child, d).text.strip()
        if t:
            parts.append(t)
    elif child.tag.endswith("}tbl"):
        for row in Table(child, d).rows:
            parts.append(" | ".join(c.text.strip().replace("\n", " ") for c in row.cells))
paper = "\n".join(parts)
print("extracted chars:", len(paper), flush=True)

PROMPT = '''你是苛刻审稿人（一区制造工程期刊资深审稿人）。以下是待评审论文的完整中文初稿（docx 排版版，含 8 张图与 13 张表，图已嵌入文档但你可能看不到图像内容，请基于文本与表格内容评估图表设计是否合理）。请直接基于论文文本评审，严禁调用任何工具/skill/数据库，禁止联网。请输出：
一、12 维分项评分（每维10分+论文内证据+扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。
二、总分（百分制）。
三、一区可发表判定（是/否+理由）。
四、致命缺陷清单（最多3条，若没有写无）。
五、小修建议清单（最多5条）。
请用中文，逐项明确，不要客气。'''

msg = PROMPT + "\n\n===论文全文开始===\n" + paper + "\n===论文全文结束==="

scores = []
verdicts = []
for run in range(1, 9):
    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": msg}],
               "stream": False,
               "options": {"num_ctx": 32768, "temperature": TEMPERATURE}}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=5400).read().decode())
    out = resp["message"]["content"]
    pathlib.Path(f"{OUT_PREFIX}{run}.md").write_text(out, encoding="utf-8-sig")

    score = None
    m = re.search(r"总分[^0-9]{0,30}([0-9]{1,3}(?:\.[0-9]+)?)\s*/\s*100", out)
    if not m:
        m = re.search(r"总分[^0-9]{0,30}([0-9]{1,3}(?:\.[0-9]+)?)\s*[（(]([0-9]{1,3}(?:\.[0-9]+)?)%", out)
        if m:
            score = float(m.group(2))
    if not m:
        m = re.search(r"总分[^0-9]{0,30}([0-9]{1,3}(?:\.[0-9]+)?)\s*分", out)
    if m and score is None:
        score = float(m.group(1))
    if score is None:
        m = re.search(r"总分[^0-9]{0,30}[0-9.]+[（(]约?([0-9]{1,3}(?:\.[0-9]+)?)%", out)
        if m:
            score = float(m.group(1))
    verdict = bool(re.search(r"一区可发表判定[^是]{0,8}是", out))
    scores.append(score)
    verdicts.append(verdict)
    print(f"run{run}: score={score} q1={verdict} chars={len(out)}", flush=True)

valid = [s for s in scores if s is not None]
n_yes = sum(verdicts)
print("SCORES=", scores, "MEDIAN=", statistics.median(valid) if valid else None)
print(f"Q1_YES={n_yes}/8（目标 ≥5）")
pathlib.Path(r"C:\Users\<user>\Desktop\灵境制造（上线版）\docs\review_outputs\review_v5_verdicts.json").write_text(
    json.dumps({"scores": scores, "verdicts": verdicts, "median": statistics.median(valid) if valid else None,
                "q1_yes": n_yes}, ensure_ascii=False, indent=2), encoding="utf-8")
