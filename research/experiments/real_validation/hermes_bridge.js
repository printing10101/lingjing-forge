// =============================================================================
// hermes_bridge.js — Hermes 本地 agent 桥接
// 弥补 DSH 缺 Hermes 的工具/技能生态（browser / computer_use / cronjob / delegation /
// 127 skills / MCP 服务）。调用 Hermes 的 run_agent.AIAgent 执行工具循环任务。
// Hermes venv: C:/Users/Lenovo/AppData/Local/hermes/hermes-agent/venv/
// =============================================================================

import { execFileSync } from 'node:child_process';
import { writeFileSync, unlinkSync, existsSync } from 'node:fs';

export const HERMES_PY = 'C:/Users/Lenovo/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe';
export const HERMES_DIR = 'C:/Users/Lenovo/AppData/Local/hermes/hermes-agent';

export async function runAgentConversation(prompt, { model = 'deepseek-v4-flash', timeoutMs = 300000 } = {}) {
  const tmpPy = 'C:/Users/Lenovo/AppData/Local/Temp/hermes_bridge_' + Date.now() + '.py';
  const tmpTxt = 'C:/Users/Lenovo/AppData/Local/Temp/hermes_prompt_' + Date.now() + '.txt';
  writeFileSync(tmpTxt, prompt, 'utf8');
  const lines = [
    'import sys',
    'sys.path.insert(0, "' + HERMES_DIR + '")',
    'from run_agent import AIAgent',
    'agent = AIAgent(model="' + model + '")',
    'prompt = open(r"' + tmpTxt + '", encoding="utf-8").read()',
    'r = agent.run_conversation(prompt)',
    'print(r if isinstance(r, str) else str(r))',
  ].join(String.fromCharCode(10));
  writeFileSync(tmpPy, lines, 'utf8');
  try {
    const out = execFileSync(HERMES_PY, [tmpPy], { encoding: 'utf8', timeout: timeoutMs, stdio: ['ignore', 'pipe', 'pipe'] });
    return { ok: true, output: out };
  } catch (e) {
    return { ok: false, error: String(e.message || e).slice(0, 400), stderr: String(e.stderr || '').slice(-600) };
  } finally {
    if (existsSync(tmpPy)) unlinkSync(tmpPy);
    if (existsSync(tmpTxt)) unlinkSync(tmpTxt);
  }
}