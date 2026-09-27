# -*- coding: utf-8 -*-
import re

src = open('C:/Users/Lenovo/AppData/Local/Temp/os_bundle.js', encoding='utf-8', errors='ignore').read()

# 找 init( 和 prompt_async 的完整方法定义
for kw in ['init(t,n)', 'prompt_async', 'method:"prompt_async"', 'prompt(t,n)']:
    idx = src.find(kw)
    print(f'=== {kw} @ {idx} ===')
    if idx >= 0:
        seg = src[idx:idx + 1800]
        # 提取方法签名可读化
        print(seg[:1800])
        print()
