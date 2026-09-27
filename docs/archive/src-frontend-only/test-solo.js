/**
 * src-frontend-only/test-solo.js
 * 测试 Solo 模式能否独立启动
 * 
 * 用法:
 * 1. 确保 .dsh-workspaces/ 目录已存在
 * 2. 运行：.\test-solo.js
 * 3. 验证 http://localhost:1420 能否正常访问
 */

import { existsSync } from 'fs';
import { resolve } from 'path';

const soloDir = resolve(__dirname);

console.log(' 测试 Solo 模式启动...\n');

// 检查必要文件
const requiredFiles = [
  'index.html',
  'main.ts',
  'App.vue',
  'router/index.ts',
  'views/SoloWorkspace.vue',
];

let allFilesExist = true;
console.log(' 检查必要文件:');
for (const file of requiredFiles) {
  const filePath = resolve(soloDir, file);
  const exists = existsSync(filePath);
  console.log(`  ${exists ? '' : ''} ${file}`);
  if (!exists) allFilesExist = false;
}

if (!allFilesExist) {
  console.log('\n 文件缺失，无法启动 Vite');
  process.exit(1);
}

console.log('\n 所有必要文件存在！');
console.log('\n 下一步操作:');
console.log('1. 打开终端，运行：cd src-frontend-only && pnpm install');
console.log('2. 然后运行：pnpm run dev');
console.log('3. 在浏览器中打开 http://localhost:1420');
console.log('4. 验证:');
console.log('  - SoloWorkspace 页面能否正常加载');
console.log('  - AISoloChat 组件是否显示在右侧');
console.log('  - RealtimePreview 组件是否工作');
console.log('  - 快捷键 Ctrl+K 是否生效');
console.log('');
console.log('如果一切正常，Solo 设计模式已成功设置！');
console.log('如需关闭，按 Ctrl+C 停止 Vite 服务器');
