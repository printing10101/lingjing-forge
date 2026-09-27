# Tauri 桌面应用 - 资源文件

将以下图标文件放在本目录下（用于 Windows MSI/NSIS 安装包）：

| 文件名 | 尺寸 | 用途 |
|--------|------|------|
| `32x32.png` | 32x32 | 任务栏/窗口 |
| `128x128.png` | 128x128 | 资源管理器 |
| `128x128@2x.png` | 256x256 | 高 DPI 资源管理器 |
| `icon.ico` | 多尺寸 | Windows 应用图标 |
| `icon.icns` | 多尺寸 | macOS 应用图标 |

可以使用 [Tauri Icon Generator](https://tauri.app/v1/guides/distribution/updater#genericons) 命令生成：

```bash
# 安装 tauri-cli (如未安装)
cargo install tauri-cli --version "^2.0"

# 从单一 PNG 生成全套图标
npx @tauri-apps/cli icon path/to/source.png
```

## 临时方案

构建前如果某些图标缺失，可以先放置以下占位文件（1x1 透明 PNG）：

```bash
# Linux / macOS
for size in 32x32 128x128 128x128@2x; do
  cp placeholder.png icons/${size}.png
done
cp placeholder.png icons/icon.ico
cp placeholder.png icons/icon.icns
```

正式发布前请替换为真实的应用图标。
