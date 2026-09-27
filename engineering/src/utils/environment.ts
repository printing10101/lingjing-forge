/**
 * 运行环境检测（单一来源）。
 *
 * 兼容两种 Tauri v2 注入标记：
 * - `__TAURI_INTERNALS__`：webview 始终注入的内部标记；
 * - `__TAURI__`：`withGlobalTauri: true`（本项目已开启）时注入的全局对象，
 *   测试环境也常以它模拟 Tauri。
 * 取两者并集可覆盖全部 Tauri 场景；检测只判断标记是否存在，
 * 不调用任何内部方法，Web/测试环境返回 false，调用方据此跳过
 * @tauri-apps/api 的动态导入。
 */
export function isTauriEnv(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as Window & {
    __TAURI_INTERNALS__?: unknown
    __TAURI__?: unknown
  }
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__)
}
