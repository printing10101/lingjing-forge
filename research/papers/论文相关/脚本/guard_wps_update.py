"""WPS 更新进程守护脚本（方案 C，用户态实现）。

背景：
    凌晨 WPS Office 自动更新（wpsupdate.exe / LeASPacWorker.exe）会触发系统会话
    清理逻辑，导致同会话下的 Python 实验进程被终止。当前用户非管理员，无法通过
    Disable-ScheduledTask 或修改 HKLM 注册表禁用更新任务。

策略：
    在用户态运行守护进程，每 30 秒扫描一次，强制 kill 以下 WPS 后台进程：
      - wpsupdate.exe        WPS 自动更新主进程
      - LeASPacWorker.exe    WPS 推送服务
      - wpscloudsvr.exe      WPS 云服务
      - wpscenter.exe        WPS 中心服务
      - wpsnotify.exe        WPS 通知服务
      - wpsupload.exe        WPS 上传服务

    不 kill 主 wps.exe / et.exe / wpp.exe（用户可能正在使用 WPS 编辑文档）。

运行方式：
    # 后台运行 24 小时（86400 秒）
    pythonw guard_wps_update.py --duration 86400

    # 前台运行，日志输出到控制台
    python guard_wps_update.py --duration 86400 --verbose

    # 自定义日志文件
    python guard_wps_update.py --duration 86400 --log guard.log

注意：
    本脚本仅作为非管理员环境下的补偿措施。根本解决方案是以管理员身份禁用
    WpsUpdateLogonTask_Lenovo / WpsUpdateTask_Lenovo / WpsWakeWnsLogonTask 三个
    任务计划，并配置 Windows Update AUOptions=1。
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil  # 已是 lnn_workflow 依赖，无需额外安装

# === 守护目标：WPS 后台更新/云服务进程（不 kill 用户正在用的 wps.exe/et.exe/wpp.exe）===
WPS_BACKGROUND_PROCESSES = {
    "wpsupdate.exe",       # WPS 自动更新主进程
    "LeASPacWorker.exe",   # WPS 推送服务
    "wpscloudsvr.exe",     # WPS 云服务
    "wpscenter.exe",       # WPS 中心服务
    "wpsnotify.exe",       # WPS 通知服务
    "wpsupload.exe",       # WPS 上传服务
    "wpsminisvr.exe",      # WPS 迷你服务
    "ksolaunch.exe",       # WPS 启动器
    "kxmail.exe",          # WPS 邮件服务
    "wpscloudlaunch.exe",  # WPS 云启动器
}

# Windows Update 相关进程（用户态可 kill 的部分）
WINDOWS_UPDATE_PROCESSES = {
    "usoclient.exe",       # Update Orchestrator Client
    "wuauclt.exe",         # Windows Update Agent
    # 注意：不 kill TrustedInstaller.exe / TiWorker.exe（系统级，需管理员）
}

SCAN_INTERVAL_SECONDS = 30  # 每 30 秒扫描一次


def setup_logger(log_file: str = None, verbose: bool = False) -> logging.Logger:
    """配置日志：同时输出到文件和控制台（verbose 时）。

    注意：logger 级别必须设为 DEBUG（而非 INFO），否则 logger.debug() 的扫描
    记录会在源头被过滤，FileHandler 即使设为 DEBUG 也收不到消息。verbose 参数
    仅控制控制台输出级别，不影响文件日志级别。
    """
    logger = logging.getLogger("guard_wps")
    logger.setLevel(logging.DEBUG)  # 始终 DEBUG，让 FileHandler 能收到所有消息
    logger.handlers.clear()

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件日志
    if log_file:
        log_path = Path(log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    else:
        # 默认日志路径：脚本同目录下 guard_wps_update.log
        default_log = Path(__file__).resolve().parent / "guard_wps_update.log"
        fh = logging.FileHandler(default_log, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    # 控制台日志（verbose 模式）
    if verbose:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


def kill_target_processes(targets: set, logger: logging.Logger) -> int:
    """扫描并 kill 指定名称的进程，返回 kill 数量。"""
    killed = 0
    for proc in psutil.process_iter(attrs=["pid", "name", "create_time"]):
        try:
            name = proc.info["name"] or ""
            if name.lower() in targets:
                pid = proc.info["pid"]
                # 安全检查：不 kill 自己
                if pid == os.getpid():
                    continue
                logger.info(f"[KILL] {name} (PID={pid})")
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as e:
            logger.debug(f"扫描进程异常: {e}")
            continue
    return killed


def guard_loop(duration_seconds: int, logger: logging.Logger) -> None:
    """守护循环：每 SCAN_INTERVAL_SECONDS 秒扫描一次，持续 duration_seconds 秒。"""
    start_time = datetime.now()
    end_time = start_time + timedelta(seconds=duration_seconds)
    logger.info(f"=" * 60)
    logger.info(f"WPS 更新守护脚本启动")
    logger.info(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"预计结束: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"扫描间隔: {SCAN_INTERVAL_SECONDS}s")
    logger.info(f"守护目标: {len(WPS_BACKGROUND_PROCESSES)} 个 WPS 后台进程 + "
                f"{len(WINDOWS_UPDATE_PROCESSES)} 个 Windows Update 进程")
    logger.info(f"=" * 60)

    all_targets = WPS_BACKGROUND_PROCESSES | WINDOWS_UPDATE_PROCESSES
    total_killed = 0
    scan_count = 0

    while datetime.now() < end_time:
        scan_count += 1
        try:
            killed = kill_target_processes(all_targets, logger)
            total_killed += killed
            if killed > 0:
                logger.info(f"[扫描 #{scan_count}] 本次 kill {killed} 个进程，"
                            f"累计 {total_killed}")
            else:
                logger.debug(f"[扫描 #{scan_count}] 无目标进程")
        except Exception as e:
            logger.error(f"[扫描 #{scan_count}] 异常: {e}", exc_info=True)

        # 分段 sleep，便于快速响应 Ctrl+C
        for _ in range(SCAN_INTERVAL_SECONDS):
            if datetime.now() >= end_time:
                break
            time.sleep(1)

    logger.info(f"=" * 60)
    logger.info(f"守护脚本结束")
    logger.info(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"总扫描次数: {scan_count}, 累计 kill 进程数: {total_killed}")
    logger.info(f"=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="WPS 更新进程守护脚本（用户态补偿措施）"
    )
    parser.add_argument(
        "--duration", type=int, default=86400,
        help="守护持续时间（秒），默认 86400（24 小时）"
    )
    parser.add_argument(
        "--log", type=str, default=None,
        help="日志文件路径，默认为脚本同目录 guard_wps_update.log"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细输出（同时输出到控制台）"
    )
    args = parser.parse_args()

    logger = setup_logger(log_file=args.log, verbose=args.verbose)

    try:
        guard_loop(args.duration, logger)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，守护脚本退出")
        sys.exit(0)
    except Exception as e:
        logger.error(f"守护脚本异常退出: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
