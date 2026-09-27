// =============================================================================
// memory_bridge.js — 持久记忆桥接（对标 Hermes 的 MEMORY.md / .hermes-memory.md）
// -----------------------------------------------------------------------------
// 用途：让 DSH agent 跨会话保留项目状态（Hermes 有 .hermes-memory.md + mem0，
// DSH 默认无持久记忆）。本插件提供项目记忆文件的读写，文件为人类可读 Markdown，
// 与 Hermes 的 MEMORY.md 模式一致，可互相引用。
//
// 文件：项目根 .dsh-memory.md（DSH 记忆）+ 可同步读 .hermes-memory.md（Hermes 记忆）
// =============================================================================

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';

export const MEMORY_FILE = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/.dsh-memory.md';
export const HERMES_MEMORY_FILE = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/.hermes-memory.md';

export function readMemory(file = MEMORY_FILE) {
  if (!existsSync(file)) return '';
  return readFileSync(file, 'utf8');
}

export function readHermesMemory() {
  return readMemory(HERMES_MEMORY_FILE);
}

/** 追加/更新记忆段落：按标题幂等替换，无则追加 */
export function writeMemorySection(title, content, file = MEMORY_FILE) {
  const sep = '\n\n';
  let text = '';
  if (existsSync(file)) text = readFileSync(file, 'utf8');
  const heading = '## ' + title;
  // 幂等替换已有段落
  const re = new RegExp(heading + '[\\s\\S]*?(?=\n## |\n# |$)', '');
  const block = heading + '\n' + content.trim() + '\n';
  if (text.includes(heading)) {
    text = text.replace(re, block);
  } else {
    text = (text.trim() ? text.trim() + sep : '') + block;
  }
  writeFileSync(file, text, 'utf8');
  return text;
}

export function appendMemory(note, file = MEMORY_FILE) {
  const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
  const line = '- [' + stamp + '] ' + note.trim();
  let text = existsSync(file) ? readFileSync(file, 'utf8') : '';
  text = text.trim() + '\n' + line + '\n';
  writeFileSync(file, text, 'utf8');
  return line;
}
