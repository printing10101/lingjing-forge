"""
Version Consistency Mechanism Tests

Tests for the complete version detection, synchronization, and auto-restart system.
2026-09-05: 自仓库根 tests/ 上提（原文件在 V2.7.0 解耦后 sys.path 与 src-tauri
路径均已失效，从未被 testpaths 收集）；跨文件一致性以 scripts/version_sync.py
CI 门禁为准，此处保留 app.version 模块级行为与关键文件版本对齐检查。
"""
import unittest
import json
import re
from unittest.mock import patch
from pathlib import Path

# 仓库根 = engineering/python/tests/unit/ 向上 4 级
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


class TestVersionModule(unittest.TestCase):
    """Test Python version.py module."""

    def test_version_file_not_found(self):
        """Test VERSION file not found fallback."""
        with patch.object(Path, 'exists', return_value=False):
            from app.version import _load_version
            version = _load_version()
            self.assertEqual(version, "0.0.0")

    def test_get_commit_hash_git_not_available(self):
        """Test commit hash fallback when git is not available."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            from app.version import _get_commit_hash
            commit = _get_commit_hash()
            self.assertIsNone(commit)

    def test_get_version_info_structure(self):
        """Test get_version_info returns correct structure."""
        from app.version import get_version_info
        info = get_version_info()

        self.assertIn('version', info)
        self.assertIn('commit', info)
        self.assertIsInstance(info['version'], str)
        self.assertIsInstance(info['commit'], str)
        self.assertTrue(len(info['version']) > 0)


class TestVersionSynchronization(unittest.TestCase):
    """Test version synchronization script."""

    def setUp(self):
        """Set up project root path."""
        self.version_file = _PROJECT_ROOT / "VERSION"
        self.cargo_toml = _PROJECT_ROOT / "engineering" / "src-tauri" / "Cargo.toml"
        self.package_json = _PROJECT_ROOT / "engineering" / "package.json"

    def test_version_file_exists(self):
        """Test VERSION file exists at project root."""
        self.assertTrue(self.version_file.exists(), "VERSION file must exist at project root")

    def test_version_format_semver(self):
        """Test VERSION file contains valid SemVer format."""
        version = self.version_file.read_text().strip()
        self.assertTrue(
            re.match(r'^\d+\.\d+\.\d+$', version),
            f"Version '{version}' must be valid SemVer format (x.y.z)"
        )

    def test_cargo_toml_version_matches(self):
        """Test Cargo.toml version matches VERSION file."""
        version = self.version_file.read_text().strip()
        cargo_content = self.cargo_toml.read_text(encoding='utf-8')

        match = re.search(r'^version\s*=\s*"([^"]+)"', cargo_content, re.MULTILINE)
        self.assertIsNotNone(match, "Cargo.toml must declare a version")
        self.assertEqual(
            version, match.group(1),
            f"VERSION file ({version}) must match Cargo.toml ({match.group(1)})"
        )

    def test_package_json_version_matches(self):
        """Test package.json version matches VERSION file."""
        version = self.version_file.read_text().strip()
        pkg = json.loads(self.package_json.read_text(encoding='utf-8'))

        self.assertEqual(
            version, pkg['version'],
            f"VERSION file ({version}) must match package.json ({pkg['version']})"
        )


if __name__ == '__main__':
    unittest.main()
