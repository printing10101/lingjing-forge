/**
 * Vite CSP Nonce Plugin
 *
 * 在构建时为 HTML 注入随机 CSP nonce，替代 'unsafe-inline'。
 *
 * 用法:
 *   1. 在 HTML meta 标签中使用占位符 __CSP_NONCE__
 *      <meta ... content="script-src 'self' 'nonce-__CSP_NONCE__'; ..." />
 *   2. 本插件会在 build 时生成随机 nonce 并替换占位符
 *   3. 同时为内联 <script> 标签添加 nonce 属性
 *
 * 安全说明:
 *   - nonce 在每个构建中随机生成，攻击者无法预测
 *   - 开发模式下使用固定 nonce "dev" 以简化调试（HMR 注入的内联脚本需要 nonce）
 *   - 生产模式使用 crypto.randomUUID() 生成 128-bit 随机 nonce
 */

import type { Plugin } from 'vite'
import crypto from 'crypto'

export function cspNoncePlugin(): Plugin {
  const isDev = process.env.NODE_ENV === 'development' || process.env.__DEV__ === 'true'
  // 开发模式使用固定 nonce（Vite HMR 注入的内联脚本也需要 nonce）
  // 生产模式使用随机 nonce（每次构建唯一）
  const nonce = isDev ? 'dev-nonce-vite-hmr' : crypto.randomBytes(16).toString('base64url')

  return {
    name: 'csp-nonce',
    enforce: 'post',

    transformIndexHtml: {
      order: 'post' as const,
      handler(html: string) {
        // 1. 替换 CSP meta 中的占位符
        let result = html.replace(/__CSP_NONCE__/g, nonce)

        // 2. 为内联 <script> 标签注入 nonce 属性
        //    匹配没有 src 属性的 <script> 标签（即内联脚本）
        result = result.replace(
          /<script(?![^>]*\bsrc\s*=)([^>]*)>/gi,
          (match, attrs) => {
            // 避免重复添加 nonce
            if (/nonce\s*=/i.test(attrs)) return match
            return `<script${attrs} nonce="${nonce}">`
          },
        )

        // 3. 为内联 <style> 标签注入 nonce 属性
        result = result.replace(
          /<style(?![^>]*\bnonce\s*=)([^>]*)>/gi,
          (match, attrs) => `<style${attrs} nonce="${nonce}">`,
        )

        return result
      },
    },
  }
}
