"""技能文件监听器 - 热更新支持。"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.config.limits import DEFAULT_THREAD_JOIN_TIMEOUT_SEC
from .models import SkillLevel

if TYPE_CHECKING:
    from .loader import SkillLoader

logger = logging.getLogger(__name__)

# 文件监听线程的统一 join 超时（秒）。用于 stop() 清理路径，
# 已迁移至 app.config.limits 集中管理。


class SkillFileWatcher:
    """技能文件监听器 - 检测文件变化并触发重新加载。"""

    def __init__(self, skills_dir: str, loader: "SkillLoader", poll_interval: float = 2.0):
        self.skills_dir = skills_dir
        self.loader = loader
        self.poll_interval = poll_interval
        self._mtime_cache: dict[str, float] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """启动文件监听线程。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info("SkillFileWatcher started (poll_interval=%.1fs)", self.poll_interval)

    def stop(self) -> None:
        """停止文件监听线程。"""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT_SEC)
        logger.info("SkillFileWatcher stopped")

    def _watch_loop(self) -> None:
        """文件监听主循环。

        必须宽捕异常：插件回调抛出的任意异常（如 ValueError）若逃逸
        会杀死监视线程且永不重启（threading excepthook 只打 stderr，
        不进日志系统），技能热更新将静默失效。
        """
        while not self._stop_event.is_set():
            try:
                self._scan_changes()
            except Exception:
                logger.exception("SkillFileWatcher scan error")
            self._stop_event.wait(self.poll_interval)

    def _scan_changes(self) -> None:
        """扫描文件变化。"""
        current_files: dict[str, float] = {}

        for root, dirs, files in os.walk(self.skills_dir):
            for f in files:
                if f.endswith(".md"):
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                        current_files[fp] = mtime
                    except OSError as stat_err:
                        logger.debug(
                            "Failed to stat skill file %s: %s",
                            fp,
                            stat_err,
                            exc_info=True,
                        )

        for fp, mtime in current_files.items():
            old_mtime = self._mtime_cache.get(fp)
            if old_mtime is None:
                logger.info("New skill file detected: %s", fp)
                self._handle_file_event(fp, "created")
            elif mtime > old_mtime:
                logger.info("Skill file modified: %s", fp)
                self._handle_file_event(fp, "modified")

        for fp in self._mtime_cache:
            if fp not in current_files:
                logger.info("Skill file removed: %s", fp)
                self._handle_file_event(fp, "deleted")

        self._mtime_cache = current_files

    def _handle_file_event(self, file_path: str, event: str) -> None:
        """处理文件事件。"""
        try:
            if event == "deleted":
                skill_id = Path(file_path).stem
                self.loader.registry.remove(skill_id)
                logger.info("Skill removed via hot-reload: %s", skill_id)
            else:
                level = self._infer_level(file_path)
                skill = self.loader._load_skill_from_file(file_path, level)
                if skill:
                    self.loader.registry.register(skill)
        except (OSError, RuntimeError, ValueError, ImportError) as e:
            logger.error("Failed to handle file event %s for %s: %s", event, file_path, e)

    def _infer_level(self, file_path: str) -> SkillLevel:
        """推断技能级别。"""
        rel = os.path.relpath(file_path, self.skills_dir).replace("\\", "/")
        if rel.startswith("global/"):
            return SkillLevel.GLOBAL
        elif rel.startswith("projects/") or rel.startswith("project/"):
            return SkillLevel.PROJECT
        elif rel.startswith("agents/") or rel.startswith("agent/"):
            return SkillLevel.AGENT
        return SkillLevel.GLOBAL
