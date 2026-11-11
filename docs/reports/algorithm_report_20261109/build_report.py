"""Render the evidence-bound Markdown report into a reviewable PDF."""
from pathlib import Path
import html
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                               TableStyle, PageBreak, Image)

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'docs/paper_and_competition/F16-可核验最小改动修正与协同精化候选-2026-11-09.md'
OUTPUT = ROOT/'output/pdf/可核验CAD最小改动修正与新算法候选报告.pdf'
pdfmetrics.registerFont(TTFont('CN', 'C:/Windows/Fonts/simsun.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('Hei', 'C:/Windows/Fonts/simhei.ttf'))
pdfmetrics.registerFont(TTFont('Math', 'C:/Windows/Fonts/cambria.ttc', subfontIndex=0))
pdfmetrics.registerFontFamily('CN', normal='CN', bold='Hei', italic='CN', boldItalic='Hei')
styles = {
    'body':ParagraphStyle('body', fontName='CN', fontSize=10.4, leading=17,
                          spaceAfter=9, wordWrap='CJK'),
    'title':ParagraphStyle('title', fontName='Hei', fontSize=18, leading=27, spaceAfter=18),
    'h1':ParagraphStyle('h1', fontName='Hei', fontSize=16, leading=23, spaceAfter=15),
    'h2':ParagraphStyle('h2', fontName='Hei', fontSize=11.5, leading=18,
                       spaceBefore=6, spaceAfter=8, keepWithNext=True),
    'cell':ParagraphStyle('cell', fontName='CN', fontSize=9.1, leading=14.1, wordWrap='CJK'),
    'caption':ParagraphStyle('caption', fontName='CN', fontSize=8.8, leading=13.5,
                            spaceAfter=9, textColor=colors.HexColor('#4D5861'), wordWrap='CJK')}
cn = pdfmetrics.getFont('CN').face.charToGlyph
math = pdfmetrics.getFont('Math').face.charToGlyph
subscripts = dict(zip('₀₁₂₃₄₅₆₇₈₉ᵢⱼᵣₓ', '0123456789ijrx'))

def markup(text):
    text = html.escape(text.replace('Û', 'Û'))
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',
                  r'<link href="\2" color="#20577D">\1</link>', text)
    out = []
    for segment in re.split('(<[^>]*>)', text):
        if segment.startswith('<'):
            out.append(segment); continue
        for char in segment:
            if char in subscripts:
                out.append('<sub>'+subscripts[char]+'</sub>')
            elif char == 'ᵀ':
                out.append('<super>T</super>')
            elif ord(char) not in cn and not char.isspace():
                if ord(char) not in math:
                    raise ValueError('No glyph for '+repr(char))
                out.append('<font name="Math">'+char+'</font>')
            else:
                out.append(char)
    return ''.join(out)

story = []

def paragraph(text, style='body'):
    story.append(Paragraph(markup(text), styles[style]))

def table(rows):
    count = len(rows[0])
    if count == 7:
        widths = [130, 45, 65, 60, 60, 70, 65]
    elif count == 4:
        widths = [125, 60, 220, 90]
    else:
        first = 155 if rows[0][0] in ('原始来源', '状态') else 90
        widths = [first, 495-first]
    content = [[Paragraph(markup(('**'+cell+'**') if i == 0 else cell), styles['cell'])
                for cell in row] for i, row in enumerate(rows)]
    result = Table(content, colWidths=widths, repeatRows=1)
    result.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), .45, colors.HexColor('#CCD7DF')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EAF1F5')),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7)]))
    story.extend([result, Spacer(1, 11)])

lines = SOURCE.read_text(encoding='utf-8').splitlines()
i = 0
while i < len(lines):
    line = lines[i].strip()
    if not line:
        i += 1; continue
    if line == '<!-- page -->':
        story.append(PageBreak()); i += 1; continue
    if line.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            row = [v.strip() for v in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?', cell) for cell in row):rows.append(row)
            i += 1
        table(rows); continue
    match = re.fullmatch(r'!\[([^\]]+)\]\(([^)]+)\)', line)
    if match:
        picture = Image(str((SOURCE.parent/match.group(2)).resolve()))
        width = 495
        picture.drawHeight = picture.imageHeight*width/picture.imageWidth
        picture.drawWidth = width
        story.extend([picture, Spacer(1, 6)])
        paragraph(match.group(1), 'caption'); i += 1; continue
    if line.startswith('### '):paragraph(line[4:], 'h2')
    elif line.startswith('## '):paragraph(line[3:], 'h1')
    elif line.startswith('# '):paragraph(line[2:], 'title')
    else:paragraph(line)
    i += 1

def footer(canvas, document):
    canvas.saveState()
    canvas.setFont('CN', 8)
    canvas.setFillColor(colors.HexColor('#586771'))
    canvas.drawString(50, 29, '灵境制造 · 可核验最小改动与算法候选 · 2026-11-09')
    canvas.drawRightString(A4[0]-50, 29, str(document.page))
    canvas.restoreState()

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
SimpleDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=50, rightMargin=50,
    topMargin=43, bottomMargin=49, title='可核验CAD最小改动修正与新算法候选报告',
    author='灵境制造科研项目').build(story, onFirstPage=footer, onLaterPages=footer)
print(OUTPUT)
