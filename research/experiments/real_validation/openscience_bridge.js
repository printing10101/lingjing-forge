// =============================================================================
// openscience_bridge.js — OpenScience 本地桥接插件
// -----------------------------------------------------------------------------
// 让外部进程（如 DSH 代码 agent）直接调用本机 OpenScience（serve 于 localhost:18787）
// 并获取执行结果。协议从 SPA bundle 逆向得出：
//
//   POST /session?directory=<dir>  body:{title}            → 建会话 → {id,...}
//   POST /session/{id}/init?directory=<dir> body:{modelID} → 会话绑定模型
//   POST /session/{id}/prompt_async?directory=<dir>
//        body:{parts:[{type:"text",text}]}                 → 提交任务（异步执行）
//   GET  /session/{id}?directory=<dir>                     → 查会话/消息
//   GET  /event?directory=<dir> (SSE)                      → 事件流（可选）
//
// 用法（ESM）：
//   const b = await import('file:///.../openscience_bridge.js');
//   const ses = await b.createSession('评审任务');
//   await b.initSession(ses.id, 'ollama/qwen3:14b');
//   await b.promptAsync(ses.id, '请阅读...');
//   const r = await b.waitForResult(ses.id, 600000, {outFile});
//   console.log(r.output);   // 最后一条 assistant 文本
//   console.log(r.files);    // 检测到的输出文件（若指令要求写文件）
// =============================================================================

export const BASE = 'http://localhost:18787';
export const WORKSPACE = 'C:\\Users\\Lenovo\\Desktop\\灵境制造（上线版）';
const DIR = encodeURIComponent(WORKSPACE);
const STORAGE = 'C:/Users/Lenovo/.local/share/openscience/storage';

async function req(path, { method = 'GET', body } = {}) {
  const url = BASE + path;
  const r = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30000),
  });
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + method + ' ' + path);
  const text = await r.text();
  try { return JSON.parse(text); } catch { return text; }
}

export async function createSession(title) {
  return req('/session?directory=' + DIR, { method: 'POST', body: { title } });
}

export async function initSession(sessionId, modelID = 'qwen3:14b', providerID = 'ollama') {
  return req('/session/' + sessionId + '/init?directory=' + DIR, { method: 'POST', body: { modelID, providerID } });
}

export async function promptAsync(sessionId, text, { agent = 'research', model, noReply } = {}) {
  const body = { agent, model, parts: [{ type: 'text', text }] };
  if (noReply !== undefined) body.noReply = noReply;
  return req('/session/' + sessionId + '/prompt_async?directory=' + DIR, { method: 'POST', body });
}

export async function getSession(sessionId) {
  return req('/session/' + sessionId + '?directory=' + DIR);
}

export async function listSessions() {
  return req('/session?directory=' + DIR);
}

export async function readSessionMessages(sessionId) {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const msgDir = path.join(STORAGE, 'message', sessionId);
  if (!fs.existsSync(msgDir)) return [];
  const files = fs.readdirSync(msgDir).filter(f => f.endsWith('.json')).sort();
  const msgs = [];
  for (const f of files) {
    try {
      const j = JSON.parse(fs.readFileSync(path.join(msgDir, f), 'utf8'));
      const partDir = path.join(STORAGE, 'part', j.id);
      let text = '';
      if (fs.existsSync(partDir)) {
        for (const pf of fs.readdirSync(partDir).filter(x => x.endsWith('.json'))) {
          try {
            const p = JSON.parse(fs.readFileSync(path.join(partDir, pf), 'utf8'));
            if (p.type === 'text') text += (text ? '\n' : '') + (p.text || '');
          } catch {}
        }
      }
      msgs.push({ role: j.role, id: j.id, finish: j.finish || '', text });
    } catch {}
  }
  return msgs;
}

export async function waitForResult(sessionId, timeoutMs = 600000, { outFile } = {}) {
  const fs = await import('node:fs');
  const start = Date.now();
  let lastAssistLen = 0;
  let stallCount = 0;
  while (Date.now() - start < timeoutMs) {
    if (outFile) {
      // 有输出文件时：只等文件出现；同时持续记录进度
      if (fs.existsSync(outFile)) {
        return { status: 'file-ready', elapsedMs: Date.now() - start, output: fs.readFileSync(outFile, 'utf8'), files: [outFile] };
      }
      if ((Date.now() - start) % 60000 < 4000) {
        const msgs = await readSessionMessages(sessionId);
        const nAssist = msgs.filter(m => m.role === 'assistant').length;
        console.log('[progress] ' + Math.round((Date.now() - start) / 1000) + 's, assistant msgs: ' + nAssist);
      }
      await new Promise(r => setTimeout(r, 5000));
      continue;
    }
    const msgs = await readSessionMessages(sessionId);
    const assist = msgs.filter(m => m.role === 'assistant' && m.text);
    if (assist.length > 0) {
      const last = assist[assist.length - 1];
      if (last.text.length === lastAssistLen) {
        stallCount++;
        if (stallCount >= 3) {
          return {
            status: 'completed',
            elapsedMs: Date.now() - start,
            output: last.text,
            messages: msgs.map(m => ({ role: m.role, finish: m.finish, textLen: m.text.length })),
          };
        }
      } else {
        stallCount = 0;
        lastAssistLen = last.text.length;
      }
    }
    await new Promise(r => setTimeout(r, 4000));
  }
  const msgs = await readSessionMessages(sessionId);
  return { status: 'timeout', elapsedMs: Date.now() - start, messages: msgs.map(m => ({ role: m.role, finish: m.finish, textLen: m.text.length })) };
}
