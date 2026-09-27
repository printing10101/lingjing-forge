# -*- coding: utf-8 -*-
"""提取 Sect 5 全文：找测试矩阵（转速×切深×结果）。"""
import re

xml = open('C:/Users/Lenovo/AppData/Local/Temp/pmc_11496886.xml', encoding='utf-8', errors='ignore').read()

# 从验证节标题开始，提取全部文本（分两段看）
m = re.search(r'Experimental verification of the robotic milling', xml)
if m:
    start = m.start()
    # 找验证节结束（下一个 <sec 或 <ref-list 或 <ack）
    end_match = re.search(r'<ref-list|<ack>|<back', xml[start:])
    end = start + (end_match.start() if end_match else 60000)
    seg = xml[start:end]
    txt = re.sub(r'<[^>]+>', ' ', seg)
    txt = re.sub(r'\s+', ' ', txt).strip()
    print('验证节总长:', len(txt))
    print(txt[:5500])
    print('\n===== 中间 =====\n')
    print(txt[5500:11000])
    print('\n===== 尾部 =====\n')
    print(txt[11000:])
else:
    print('未找到')
