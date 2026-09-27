# -*- coding: utf-8 -*-
"""从 SPA bundle 挖 /session /agent /command 的请求体格式。"""
import re

src = open('C:/Users/Lenovo/AppData/Local/Temp/os_bundle.js', encoding='utf-8', errors='ignore').read()
print('bundle:', len(src)//1024, 'KB')

# 找 POST /session 调用上下文
for endpoint in ['/session', '/agent', '/command', '/event']:
    print(f'\n=== 端点 {endpoint} 相关代码 ===')
    hits = [m.start() for m in re.finditer(re.escape(endpoint), src)]
    print(f'  出现 {len(hits)} 处')
    shown = 0
    for i in hits:
        # 向前找 fetch( 或 method POST 上下文
        ctx_start = max(0, i - 600)
        ctx = src[ctx_start:i + 400]
        # 提取可读片段
        if 'POST' in ctx or 'fetch' in ctx:
            # 找 JSON 键名
            keys = re.findall(r'["\'](\w+)["\']\s*:', ctx)
            print(f'  @{i} 附近键名: {list(dict.fromkeys(keys))[:15]}')
            shown += 1
            if shown >= 4:
                break
