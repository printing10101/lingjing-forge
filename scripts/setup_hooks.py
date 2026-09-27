#!/usr/bin/env python3
"""
Setup script for pre-commit hooks and development tools.
Run: python setup_hooks.py
"""

import subprocess
import sys
from pathlib import Path


def run(cmd: str, cwd: str = None) -> int:
    """Run a shell command and return the exit code."""
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    return result.returncode


def main():
    project_root = Path(__file__).parent.parent

    print("=" * 60)
    print("Setting up pre-commit hooks for 灵境制造")
    print("=" * 60)

    # 1. Install Python pre-commit tool
    print("\n1. Installing Python pre-commit tool...")
    if run("pip install pre-commit") != 0:
        print("Warning: Failed to install pre-commit. Continuing anyway...")

    # 2. Install pre-commit hooks
    print("\n2. Installing pre-commit hooks...")
    if run("pre-commit install", cwd=str(project_root)) != 0:
        print("Warning: Failed to install pre-commit hooks. Continuing anyway...")

    # 3. Install commit-msg hook
    print("\n3. Installing commit-msg hook...")
    if run("pre-commit install --hook-type commit-msg", cwd=str(project_root)) != 0:
        print("Warning: Failed to install commit-msg hook. Continuing anyway...")

    # 4. Install Node.js dependencies for husky
    print("\n4. Installing Node.js dependencies...")
    if run("pnpm install", cwd=str(project_root)) != 0:
        print("Warning: Failed to install Node.js dependencies. Continuing anyway...")

    # 5. Setup husky
    print("\n5. Setting up husky...")
    if run("pnpm exec husky", cwd=str(project_root)) != 0:
        print("Warning: Failed to setup husky. Continuing anyway...")

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print("\nTo verify installation:")
    print("  Python: pre-commit run --all-files")
    print("  Node:   pnpm lint-staged")
    print("\nTo skip pre-commit hooks (emergency only):")
    print("  git commit --no-verify")
    print("\nCommit message format:")
    print("  type(scope): subject")
    print("  Examples:")
    print("    feat(frontend): add user profile page")
    print("    fix(backend): resolve memory leak in API")
    print("    docs(readme): update installation guide")


if __name__ == "__main__":
    main()
