#!/usr/bin/env node
/**
 * 清理 locale 文件中指定顶层区块的死 key。
 *
 * 用法：node scripts/cleanup_i18n_keys.mjs <locale-file> <key1,key2,...>
 * 区块定位：顶层 section 形如 "  name: {"，结束于 "  },"（两空格缩进）。
 * 幂等：key 不存在时无修改。
 */
import { readFileSync, writeFileSync } from 'node:fs'

const [file, keysArg] = process.argv.slice(2)
if (!file || !keysArg) {
  console.error('usage: node scripts/cleanup_i18n_keys.mjs <locale-file> <key1,key2,...>')
  process.exit(1)
}
const removeKeys = new Set(keysArg.split(',').map((k) => k.trim()).filter(Boolean))

const lines = readFileSync(file, 'utf8').split('\n')
const out = []
const sectionStart = /^  ([A-Za-z0-9_]+): \{/
const sectionEnd = /^  \},?\s*$/

let inSection = null
let removed = []
for (const line of lines) {
  if (inSection) {
    if (sectionEnd.test(line)) {
      inSection = null // 区块结束，丢弃结束行
    }
    continue // 区块内所有行都丢弃
  }
  const m = line.match(sectionStart)
  if (m && removeKeys.has(m[1])) {
    inSection = m[1]
    removed.push(m[1])
    continue
  }
  out.push(line)
}

writeFileSync(file, out.join('\n'))
console.log(`${file}: removed [${removed.join(', ')}], ${lines.length} -> ${out.length} lines`)
