#!/usr/bin/env python3
"""
Version Synchronization Script

Detects and synchronizes version numbers across all key files in the project.
Supports --check (verify consistency) and --set (update all to target version).

Source of truth: root VERSION file

Usage:
    python scripts/version_sync.py --check          Verify all versions match
    python scripts/version_sync.py --set 1.8.0      Set all versions to 1.8.0
    python scripts/version_sync.py --show            Display current versions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionFile:
    def __init__(self, relative_path: str, description: str, reader, writer):
        self.relative_path = relative_path
        self.description = description
        self._reader = reader
        self._writer = writer

    def read(self) -> str | None:
        path = PROJECT_ROOT / self.relative_path
        if not path.exists():
            return None
        return self._reader(path)

    def write(self, version: str) -> bool:
        path = PROJECT_ROOT / self.relative_path
        if not path.exists():
            return False
        return self._writer(path, version)


def _read_raw(path: Path) -> str:
    return path.read_text().strip()


def _read_json_key(key_path: str):
    def reader(path: Path) -> str | None:
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = key_path.split(".")
        value = data
        for k in keys:
            value = value.get(k, {})
        return str(value) if value else None

    return reader


def _read_regex(pattern: str, group: int = 1):
    def reader(path: Path) -> str | None:
        content = path.read_text(encoding="utf-8")
        match = re.search(pattern, content)
        return match.group(group) if match else None

    return reader


def _write_raw(path: Path, version: str) -> bool:
    path.write_text(version + "\n")
    return True


def _write_json_key(key_path: str):
    def writer(path: Path, version: str) -> bool:
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = key_path.split(".")
        target = data
        for k in keys[:-1]:
            target = target[k]
        old = target[keys[-1]]
        if old == version:
            return False
        target[keys[-1]] = version
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True

    return writer


def _write_regex(pattern: str, replacement_template: str):
    def writer(path: Path, version: str) -> bool:
        content = path.read_text(encoding="utf-8")
        replacement = replacement_template.format(version=version)
        new_content, count = re.subn(pattern, replacement, content)
        if count == 0 or new_content == content:
            return False
        path.write_text(new_content, encoding="utf-8")
        return True

    return writer


def _write_config_py(path: Path, version: str) -> bool:
    content = path.read_text(encoding="utf-8")
    # P0-5 修复：匹配 _env("APP_VERSION", "x.y.z") 写法（原正则缺逗号）
    pattern = r'(APP_VERSION",\s*")([\d.]+)(")'
    new_content, count = re.subn(pattern, rf"\g<1>{version}\g<3>", content)
    if count == 0 or new_content == content:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _write_main_py(path: Path, version: str) -> bool:
    content = path.read_text(encoding="utf-8")
    pattern = r'(version=")([\d.]+)(")'
    new_content, count = re.subn(pattern, rf"\g<1>{version}\g<3>", content)
    if count == 0 or new_content == content:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _write_cargo_toml(path: Path, version: str) -> bool:
    content = path.read_text(encoding="utf-8")
    pattern = r'(?m)^version\s*=\s*"([^"]+)"'
    new_content, count = re.subn(pattern, f'version = "{version}"', content, count=1)
    if count == 0 or new_content == content:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _read_cargo_toml(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


VERSION_FILES: list[VersionFile] = [
    VersionFile(
        "VERSION",
        "Root VERSION file (source of truth)",
        _read_raw,
        _write_raw,
    ),
    VersionFile(
        # P0-5 修复：阶段2解耦后前端/桌面位于 engineering/ 下
        "engineering/package.json",
        "package.json (Node.js/Tauri frontend)",
        _read_json_key("version"),
        _write_json_key("version"),
    ),
    VersionFile(
        "engineering/src-tauri/Cargo.toml",
        "Cargo.toml (Rust backend)",
        _read_cargo_toml,
        _write_cargo_toml,
    ),
    VersionFile(
        "engineering/src-tauri/tauri.conf.json",
        "tauri.conf.json (Tauri config)",
        _read_json_key("version"),
        _write_json_key("version"),
    ),
    VersionFile(
        # P0-5 修复：config.py 实际位于 app/config/app_config.py，且写法为 _env("APP_VERSION", "x.y.z")
        "engineering/python/app/config/app_config.py",
        "app_config.py (APP_VERSION default)",
        _read_regex(r'APP_VERSION",\s*"([\d.]+)"'),
        _write_config_py,
    ),
    VersionFile(
        "engineering/python/app/main.py",
        "main.py (FastAPI version string)",
        _read_regex(r'version="([\d.]+)"'),
        _write_main_py,
    ),
    VersionFile(
        "docs/api/openapi.json",
        "openapi.json (API docs version)",
        _read_json_key("info.version"),
        _write_json_key("info.version"),
    ),
    VersionFile(
        # 2026-09-05 补录：docs-site 版本此前停更于 2.7.0（不在同步清单内漏更）
        "docs-site/package.json",
        "docs-site/package.json (VitePress site)",
        _read_json_key("version"),
        _write_json_key("version"),
    ),
]


def _find_changelog_files() -> list[Path]:
    docs_dir = PROJECT_ROOT / "docs"
    if not docs_dir.is_dir():
        return []
    return sorted(docs_dir.glob("变更摘要*.md"))


def get_all_versions() -> dict[str, str | None]:
    results = {}
    for vf in VERSION_FILES:
        results[vf.relative_path] = vf.read()

    for changelog in _find_changelog_files():
        content = changelog.read_text(encoding="utf-8")
        key = f"docs/{changelog.name}"
        match = re.search(r"文档版本[\*]*[：:]\s*V?([\d.]+)", content)
        results[key] = match.group(1) if match else None

    return results


def show_versions() -> None:
    versions = get_all_versions()
    max_len = max(len(k) for k in versions)
    for path, ver in versions.items():
        status = ver if ver else "NOT FOUND"
        print(f"  {path:<{max_len}}  {status}")


def check_consistency() -> bool:
    root_ver = (PROJECT_ROOT / "VERSION").read_text().strip()
    if not SEMVER_RE.match(root_ver):
        print(f"ERROR: Invalid version in VERSION file: {root_ver}")
        return False

    versions = get_all_versions()
    all_ok = True

    for path, ver in versions.items():
        if path == "VERSION":
            continue
        if ver is None:
            print(f"  [{path}] Version not found")
            all_ok = False
        elif ver != root_ver:
            changelog_match = re.match(r"docs/变更摘要(V[\d.]+)\.md", path)
            if changelog_match:
                doc_ver = changelog_match.group(1)
                if ver == doc_ver.lstrip("V"):
                    continue
            print(f"  [{path}] MISMATCH: {ver} (expected {root_ver})")
            all_ok = False

    if all_ok:
        print(f"  All files consistent at version {root_ver}")
    return all_ok


def set_version(new_version: str, dry_run: bool = False) -> bool:
    if not SEMVER_RE.match(new_version):
        print(f"ERROR: Invalid version format: {new_version} (expected x.y.z)")
        return False

    old_version = (PROJECT_ROOT / "VERSION").read_text().strip()
    if old_version == new_version:
        # P0-5 修复：VERSION 已为目标版本时仍应同步其他落后文件
        # （例如 main.py 的 version 串、openapi.json 等可能滞后于 VERSION）
        print(f"VERSION 已为 {new_version}，继续同步其他版本文件...")
    else:
        print(f"版本同步: {old_version} -> {new_version}")

    changed = False

    for vf in VERSION_FILES:
        current = vf.read()
        if current is None:
            print(f"  SKIP [{vf.relative_path}] file not found")
            continue
        if current != new_version:
            action = "WOULD update" if dry_run else "UPDATE"
            print(f"  {action} [{vf.relative_path}] {current} -> {new_version}")
            if not dry_run:
                if vf.write(new_version):
                    changed = True
        else:
            print(f"  OK   [{vf.relative_path}] already {new_version}")

    if not dry_run and changed:
        print(f"\nVersion synchronized: {old_version} -> {new_version}")

    return True


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Synchronize version numbers across all project files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Check all files for version consistency (read-only)")
    group.add_argument("--set", type=str, metavar="VERSION", help="Set all version references to the specified version")
    group.add_argument("--show", action="store_true", help="Display current versions across all files")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without modifying files (use with --set)"
    )

    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)

    if args.check:
        print("=== Version Consistency Check ===\n")
        ok = check_consistency()
        sys.exit(0 if ok else 1)

    elif args.set:
        print(f"=== Setting Version: {args.set} ===\n")
        ok = set_version(args.set, dry_run=args.dry_run)
        if ok:
            print("\nVerifying after sync...")
            check_consistency()
        sys.exit(0 if ok else 1)

    elif args.show:
        print("=== Current Versions ===\n")
        show_versions()


if __name__ == "__main__":
    main()
