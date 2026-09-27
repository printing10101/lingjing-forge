"""检查 GitHub Releases 最新版本（自动更新过渡方案）。

「设置 → 关于」页「检查更新」按钮的后端支撑：
- 对比当前 VERSION 与 GitHub Releases latest tag（语义化版本号）
- 网络不可用 / 解析失败时 fail-soft：返回 error 字段（短代码），不阻断页面
- 设计约束（工业级交付路线图 2.2）：updater 插件接入前的最低成本过渡方案

可配置项：
- LINGJING_UPDATE_REPO：GitHub 仓库（默认 printing10101/Virtual-Realm-Manufacturing，
  便于 fork 与测试注入）
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.version import VERSION

logger = logging.getLogger(__name__)

_DEFAULT_REPO = "printing10101/Virtual-Realm-Manufacturing"
_RELEASES_API = "https://api.github.com/repos/{repo}/releases/latest"
_RELEASES_URL = "https://github.com/{repo}/releases/latest"
_TIMEOUT_SECONDS = 5.0


def _repo() -> str:
    """GitHub 仓库标识（环境变量可覆盖，测试注入用）。"""
    return os.environ.get("LINGJING_UPDATE_REPO", _DEFAULT_REPO)


def parse_version(version: str) -> tuple[int, int, int]:
    """解析语义化版本号 → (major, minor, patch)。

    容忍 v 前缀（v2.7.0）与预发布/构建元数据后缀（2.7.0-beta.1、2.7.0+build5）。
    """
    raw = version.strip().lstrip("vV")
    main = raw.split("-")[0].split("+")[0]
    nums: list[int] = []
    for part in main.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            break
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def version_gt(a: str, b: str) -> bool:
    """a 是否严格大于 b（按语义化版本比较）。"""
    return parse_version(a) > parse_version(b)


def _fetch_latest() -> tuple[str | None, str | None]:
    """拉取 GitHub latest release 的 tag 与页面 URL；失败返回 (None, None)。"""
    repo = _repo()
    try:
        resp = httpx.get(_RELEASES_API.format(repo=repo), timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name")
        if not tag:
            return None, None
        url = data.get("html_url") or _RELEASES_URL.format(repo=repo)
        return str(tag), str(url)
    except Exception as exc:  # 网络 / HTTP / 解析失败均 fail-soft
        logger.warning("update check failed for repo %s: %s", repo, exc)
        return None, None


async def check_for_updates() -> dict[str, Any]:
    """执行一次更新检查（同步网络调用封装为 async 供 FastAPI 调用）。

    返回结构：
        current_version   当前应用版本
        latest_version    远端 latest tag（失败为 None）
        update_available  是否存在可用更新
        latest_release_url 最新 Release 页面 URL（失败为 None）
        checked_at        UTC 检查时间（ISO 8601）
        error             失败短代码："network" | "parse" | None
    """
    latest_version, latest_url = _fetch_latest()
    payload: dict[str, Any] = {
        "current_version": VERSION,
        "latest_version": latest_version,
        "update_available": latest_version is not None and version_gt(latest_version, VERSION),
        "latest_release_url": latest_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    if latest_version is None:
        payload["error"] = "network" if latest_url is None else "parse"
    return payload
