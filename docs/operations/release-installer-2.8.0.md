# 安装包构建记录（C1）：灵境制造 2.8.0 x64 NSIS

日期：2026-11-14。状态：**安装包已构建并核对指纹**；干净 Windows 机器的一次安装验证**尚未执行**（需用户授权或独立 VM）。

## 产物

| 项 | 值 |
|---|---|
| 文件 | `engineering/src-tauri/target/release/bundle/nsis/灵境制造_2.8.0_x64-setup.exe` |
| 大小 | 64 MB（67,108,864 字节量级） |
| SHA256 前缀 | `b97e161cd66f45c9…` |
| 版本 | `VERSION` = 2.8.0，与安装包文件名一致 |
| 架构 | x64 |
| 打包类型 | NSIS（`tauri.conf.json` bundle.targets 含 nsis/dmg/appimage/deb，本次只构建 nsis） |

产物位于 `target/`（已被 `engineering/src-tauri/.gitignore` 忽略，不入库）；构建命令与指纹记录在本文件，可按需重现。

## 构建命令与环境

```bash
cd engineering
pnpm tauri build --bundles nsis
```

| 组件 | 版本 |
|---|---|
| pnpm | 9.15.9 |
| cargo / rustc | 1.98.0 |
| 构建耗时 | 依赖编译 7m12s（release），端到端 9m53s |
| 构建结果 | `Finished 1 bundle`，EXIT=0 |

构建过程无网络失败、无签名步骤（当前未配置代码签名证书，安装时 Windows SmartScreen 可能提示未知发布者）。

## 已验证 / 未验证

**已验证**

- 构建退出码 0，`cargo build --release` 完成并生成 `lingjing-v4.exe`；
- NSIS 打包步骤成功（makensis）；
- 安装包版本号与 `VERSION` 文件一致；
- 产物被 git 忽略，未污染仓库。

**未验证（C1 验收缺口）**

- **干净 Windows 机器一次安装成功**：需独立虚拟机或用户授权本机安装，本轮未执行（不在未授权情况下改动用户系统）；
- **零配置启动**：需安装后实际启动应用验证（依赖 desktop_runtime 与 Python 运行时打包完整性）；
- 卸载/升级路径、非管理员权限安装行为；
- 代码签名与 SmartScreen 提示。

## 建议的验收步骤（需用户执行或授权）

1. 在干净 Win11 VM 运行安装包，记录是否需要管理员权限、耗时、报错；
2. 启动应用，确认后端 `start_server.py` 自动拉起、无手工配置；
3. 打开「图纸 → 参数 → NC」流程，用黄金案例 DXF（`data/test_fixtures/case11_perforated_plate.dxf`）跑一遍；
4. 卸载，确认无残留进程/服务。

## 复现

```bash
cd engineering && pnpm tauri build --bundles nsis
sha256sum src-tauri/target/release/bundle/nsis/*.exe
```
