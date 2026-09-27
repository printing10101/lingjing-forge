// 全局错误捕获（必须在所有其他代码之前注册）
// 用于诊断 Tauri 桌面环境下白屏问题：将任何未捕获的错误渲染到页面，避免白屏无信息。

/**
 * 完整 HTML 转义：防止错误信息中的特殊字符在 innerHTML 上下文中被解析为 HTML/JS。
 * 转义顺序要求 & 必须最先，否则后续转义产生的实体会被二次转义。
 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderFatalError(stage: string, err: unknown) {
  const msg = err instanceof Error ? `${err.name}: ${err.message}\n${err.stack ?? ''}` : String(err)
  const html = `
    <div style="position:fixed;inset:0;background:#1e1e1e;color:#ff5555;font-family:Consolas,monospace;font-size:13px;padding:20px;z-index:2147483647;overflow:auto;white-space:pre-wrap;">
      <h2 style="color:#ff9900;margin:0 0 12px;">[致命错误] ${escapeHtml(stage)}</h2>
      <div style="color:#dddddd;">${escapeHtml(msg)}</div>
      <hr style="border:0;border-top:1px solid #444;margin:16px 0;" />
      <div style="color:#888;">UA: ${escapeHtml(navigator.userAgent)}</div>
      <div style="color:#888;">URL: ${escapeHtml(location.href)}</div>
      <div style="color:#888;">Time: ${new Date().toISOString()}</div>
    </div>
  `
  // 尝试写入 body；若 body 尚未就绪，写入 html
  const target = document.body || document.documentElement
  // 如果 #app 存在，先清空它再追加错误面板
  const appEl = document.getElementById('app')
  if (appEl) appEl.innerHTML = ''
  const wrapper = document.createElement('div')
  wrapper.innerHTML = html
  target.appendChild(wrapper.firstElementChild as HTMLElement)
}

// 已知非致命告警：ResizeObserver loop 属于浏览器无害警告，不应触发致命错误页
const NON_FATAL_PATTERNS = [/resizeobserver loop/i]

function isNonFatalError(msg: string): boolean {
  return NON_FATAL_PATTERNS.some((re) => re.test(msg))
}

window.addEventListener('error', (e) => {
  const msg = e.error instanceof Error ? e.error.message : String(e.message ?? '')
  if (isNonFatalError(msg)) {
    console.warn('[main] 忽略非致命错误:', msg)
    return
  }
  renderFatalError('window.error', e.error ?? `${e.message} @ ${e.filename}:${e.lineno}:${e.colno}`)
})
window.addEventListener('unhandledrejection', (e) => {
  renderFatalError('unhandledrejection', e.reason)
})

// 正常应用启动流程
import { createApp, ref } from 'vue'
import { createPinia } from 'pinia'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import App from './App.vue'
import router from './router'
import { i18n, setLocale, type SupportedLocale } from './i18n'
import { setHttpReady } from './utils/http'
import { registerDialectManagerPlugin } from './plugins/dialect-manager'
// Element Plus 基础样式必须先于主题覆盖层加载，否则组件（按钮/表单/弹窗等）会缺失底座样式
import 'element-plus/dist/index.css'
// Apple 风格米白主题覆盖层（加载顺序在后 → 覆盖 EP 默认样式）
import './assets/styles/theme.css'

function getLocale(): string {
  return localStorage.getItem('app_locale') || 'zh-CN'
}

const elLocale = ref(getLocale() === 'en' ? en : zhCn)

function syncElLocale(locale: string) {
  elLocale.value = locale === 'en' ? en : zhCn
}

const originalSetLocale = setLocale
function setLocaleWithEl(locale: SupportedLocale) {
  originalSetLocale(locale)
  syncElLocale(locale)
}
(window as Window & { __setLocale?: typeof setLocaleWithEl }).__setLocale = setLocaleWithEl

try {
  const app = createApp(App)
  const pinia = createPinia()
  app.use(pinia)
  app.use(i18n)
  app.provide('locale', elLocale)
  app.use(router)

  app.config.globalProperties.$setLocale = setLocaleWithEl

  // 注册前端插件（dialect-manager → workspace.panel 扩展点）
  // 失败不阻断启动：插件面板是可选的，核心功能不受影响
  try {
    registerDialectManagerPlugin()
  } catch (pluginErr) {
    console.warn('[main] 前端插件注册失败（非致命）:', pluginErr)
  }

  app.mount('#app')
  // 通知 index.html 中的诊断脚本：Vue 已挂载，避免误判为白屏
  ;(window as Window & { __markVueMounted__?: () => void }).__markVueMounted__?.()
} catch (err) {
  renderFatalError('Vue mount', err)
  throw err
}

// 启动时序常量
/** HTTP 健康检查就绪延迟（毫秒）：等待后端完成初始化后再启用 HTTP 错误弹窗 */
const HTTP_READY_DELAY_MS = 3_000
/** 首屏渲染后等待帧数再关闭启动画面，避免短暂白屏 */
const SPLASHSCREEN_CLOSE_DELAY_MS = 100

// 初始化阶段完成后启用 HTTP 错误弹窗
setTimeout(() => setHttpReady(), HTTP_READY_DELAY_MS)

// Tauri 启动动画收尾
// Vue 应用挂载完成、首屏渲染就绪后，关闭原生 splashscreen 窗口并显示主窗口。
// 仅在 Tauri 环境下执行；Web 开发模式下不触发。
async function closeNativeSplashscreen() {
  const isTauri =
    typeof window !== 'undefined' &&
    Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__)
  if (!isTauri) return
  try {
    const mod = await import('@tauri-apps/api/core')
    await mod.invoke('close_splashscreen')
  } catch (err) {
    // splashscreen 关闭失败不应阻塞应用启动
    console.warn('[main] 关闭 splashscreen 失败:', err)
  }
}
// 等待一帧让首屏真正绘制后再切换，避免出现短暂白屏
requestAnimationFrame(() => {
  setTimeout(closeNativeSplashscreen, SPLASHSCREEN_CLOSE_DELAY_MS)
})
