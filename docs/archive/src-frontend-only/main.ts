/**
 * src-frontend-only/main.ts
 * Solo 模式专属入口文件
 * 
 * 与 engineering/src/main.ts 的区别：
 * - 不注入 window.__DSH_BOOT__（非 Tauri 环境）
 * - 启用 Solo 模式专属布局
 * - 全屏编辑器 + AI 对话面板
 */

import { createApp } from 'vue';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import zhCn from 'element-plus/es/locale/lang/zh-cn';

// 导入主 App
import App from './App.vue';

// 导入路由
import router from './router';

// 导入 Pinia Store
import { createPinia } from 'pinia';

// 全局配置
const app = createApp(App);

// 标记为 Solo 模式
app.config.globalProperties.$SOLO_MODE = true;
app.config.globalProperties.$isTauri = false;

// 使用插件
app.use(createPinia());
app.use(router);
app.use(ElementPlus, {
  locale: zhCn,
  size: 'default'
});

// 注册全局快捷键（Solo 模式专属）
// Ctrl + K: 打开 AI 对话面板
// Ctrl + Shift + P: 打开命令面板
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'k') {
    e.preventDefault();
    // TODO: 触发 AI 对话面板切换
  }
  
  if (e.ctrlKey && e.shiftKey && e.key === 'P') {
    e.preventDefault();
    // TODO: 触发命令面板
  }
});

// 挂载应用
app.mount('#app');

// 注册服务 Worker（可选）
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js')
    .catch((err) => console.error('[Solo Mode] Service Worker Error:', err));
}

export default app;
