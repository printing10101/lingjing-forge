// =============================================================================
// web_bridge.js — 主机网络桥接 v2（curl.exe 直连，免 PowerShell 引号坑）
// =============================================================================
import { execFileSync } from 'node:child_process';
import { readFileSync, unlinkSync, existsSync } from 'node:fs';

export async function fetchUrl(url, { maxBytes = 300000, resolveHost } = {}) {
  const tmp = 'C:/Users/Lenovo/AppData/Local/Temp/web_bridge_' + Date.now() + '.out';
  const args = ['-s', '-L', '--connect-timeout', '20', '--max-time', '60', '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0', '-o', tmp, '-w', '%{http_code} %{size_download}', url];
  if (resolveHost) {
    const [host, port, ip] = resolveHost.split(':');
    args.splice(1, 0, '--resolve', host + ':' + port + ':' + ip);
  }
  try {
    const meta = execFileSync('curl.exe', args, { encoding: 'utf8', timeout: 90000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    const [code, size] = meta.split(' ');
    if (!existsSync(tmp)) return { ok: false, status: code, error: 'no output file' };
    const data = readFileSync(tmp, 'utf8');
    unlinkSync(tmp);
    if (code !== '200') return { ok: false, status: code, error: 'HTTP ' + code, body: data.slice(0, 500) };
    return { ok: true, status: code, size: Number(size), body: data.slice(0, maxBytes), truncated: data.length > maxBytes };
  } catch (e) {
    let err = String(e.message || e);
    if (e.stderr) err += ' | ' + String(e.stderr).slice(0, 200);
    return { ok: false, error: err.slice(0, 400) };
  }
}

export async function fetchJson(url, opts) {
  const r = await fetchUrl(url, opts);
  if (!r.ok) return r;
  try { return { ...r, json: JSON.parse(r.body) }; } catch { return { ...r, json: null, parseError: 'not-json' }; }
}
