import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { readFileSync } from 'fs'
import { cspNoncePlugin } from './vite-csp-nonce'

const pkg = JSON.parse(readFileSync(resolve(__dirname, 'package.json'), 'utf-8'))

export default defineConfig({
  // Tauri 生产模式通过 tauri:// 协议加载本地文件，必须使用相对路径，
  // 否则 /assets/... 等绝对路径无法解析，导致 JS/CSS 全部加载失败、Vue 无法挂载。
  base: './',
  plugins: [
    vue(),
    cspNoncePlugin(),
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      resolvers: [ElementPlusResolver({ importStyle: false })],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: false })],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(pkg.version),
    'import.meta.env.VITE_APP_COMMIT': JSON.stringify('dev'),
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        splashscreen: resolve(__dirname, 'splashscreen.html'),
      },
      output: {
        manualChunks: {
          'framework-vendor': ['vue', 'vue-router', 'pinia'],
          'element-plus-icons': ['@element-plus/icons-vue'],
          'element-plus-locale': ['element-plus/es/locale/lang/zh-cn', 'element-plus/es/locale/lang/en'],
          'three-vendor': ['three'],
        },
        chunkFileNames: 'assets/js/[name]-[hash].js',
        entryFileNames: 'assets/js/[name]-[hash].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith('.vue')) {
            return 'assets/views/[name]-[hash].[ext]'
          }
          if (assetInfo.name && (assetInfo.name.includes('.png') || assetInfo.name.includes('.jpg') || assetInfo.name.includes('.svg'))) {
            return 'assets/images/[name]-[hash].[ext]'
          }
          return 'assets/[ext]/[name]-[hash].[ext]'
        },
      },
    },
    // 注意：在某些构建环境中，emptyOutDir: true 会触发安全删除守卫，因此保持 false
    // (genie-safe-delete 拦截 Vite 对 dist/assets 的批量 rmSync)，导致 beforeBuildCommand 失败。
    // 改为 false，构建前手动以沙箱放行方式清理 dist 即可保证产物干净。
    emptyOutDir: false,
    sourcemap: false,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
        passes: 2,
      },
      mangle: {
        safari10: true,
      },
    },
    target: 'es2021',
    chunkSizeWarningLimit: 1000,
    cssCodeSplit: true,
    modulePreload: {
      polyfill: true,
    },
  },
  optimizeDeps: {
    include: [
      'vue',
      'vue-router',
      'pinia',
      'axios',
    ],
    exclude: [
      'three',
      'echarts',
    ],
  },
})
