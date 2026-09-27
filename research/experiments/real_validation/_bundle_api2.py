# -*- coding: utf-8 -*-
import re

src = open('C:/Users/Lenovo/AppData/Local/Temp/os_bundle.js', encoding='utf-8', errors='ignore').read()

def show_ctx(pos, width=1400, label=''):
    s = max(0, pos - width // 2)
    e = min(len(src), pos + width // 2)
    seg = src[s:e]
    print(f'--- {label} @{pos} ---')
    print(seg.replace('\\n', ' ')[:width])
    print()

# /session 的所有出现，挑含 fetch/method/url 的
hits = [m.start() for m in re.finditer('/session', src)]
print('/session 出现:', len(hits))
shown = 0
for i in hits:
    ctx = src[max(0, i - 800):i + 300]
    if 'fetch(' in ctx or 'method' in ctx or 'post(' in ctx or 'JSON.stringify' in ctx:
        show_ctx(i, 1600, 'session')
        shown += 1
        if shown >= 5:
            break

# /agent 附近
hits2 = [m.start() for m in re.finditer('/agent', src)]
print('\n/agent 出现:', len(hits2))
for i in hits2[:2]:
    show_ctx(i, 1600, 'agent')

# /command 附近
hits3 = [m.start() for m in re.finditer('/command', src)]
print('\n/command 出现:', len(hits3))
for i in hits3[:2]:
    show_ctx(i, 1400, 'command')
