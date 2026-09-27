"""统一组件目录命名为 kebab-case。

将 dxf_import→dxf-import, step_import→step-import, rule_editor→rule-editor,
Copilot→copilot, CommandPalette→command-palette, Onboarding→onboarding

使用方法:
    cd engineering
    python scripts/rename_directories.py --dry-run  # 预览变更
    python scripts/rename_directories.py             # 执行
"""
import argparse
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
COMPONENTS = SRC / "components"

RENAMES = {
    "dxf_import": "dxf-import",
    "step_import": "step-import",
    "rule_editor": "rule-editor",
    "Copilot": "copilot",
    "CommandPalette": "command-palette",
    "Onboarding": "onboarding",
}


def collect_affected_files() -> list[Path]:
    """收集所有引用了待重命名目录的源文件。"""
    affected = []
    for ext in ("*.ts", "*.vue"):
        for f in SRC.rglob(ext):
            if "__pycache__" in str(f) or "node_modules" in str(f):
                continue
            text = f.read_text(encoding="utf-8")
            for old_name in RENAMES:
                pattern = f"/{old_name}/"
                if pattern in text:
                    affected.append(f)
                    break
    return sorted(set(affected))


def update_imports(files: list[Path], dry_run: bool = True) -> int:
    """更新所有文件中的导入路径。"""
    updated = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        new_text = text
        for old_name, new_name in RENAMES.items():
            new_text = new_text.replace(f"/{old_name}/", f"/{new_name}/")
        if new_text != text:
            if not dry_run:
                f.write_text(new_text, encoding="utf-8")
            updated += 1
            print(f"  {'[DRY RUN]' if dry_run else 'UPDATED'} {f.relative_to(SRC)}")
    return updated


def rename_dirs(dry_run: bool = True):
    """重命名物理目录。"""
    for old_name, new_name in RENAMES.items():
        old_path = COMPONENTS / old_name
        new_path = COMPONENTS / new_name
        if old_path.exists() and not new_path.exists():
            if not dry_run:
                old_path.rename(new_path)
            print(f"  {'[DRY RUN]' if dry_run else 'RENAMED'} {old_name} -> {new_name}")
        elif new_path.exists():
            print(f"  SKIP {old_name}: {new_name} already exists")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", default=False)
    args = parser.parse_args()

    dry_run = not args.execute

    print("=== Step 1: Collect affected files ===")
    files = collect_affected_files()
    print(f"Found {len(files)} files to update:\n")
    for f in files:
        print(f"  {f.relative_to(SRC)}")

    print(f"\n=== Step 2: {'[DRY RUN] ' if dry_run else ''}Update imports ===")
    updated = update_imports(files, dry_run=dry_run)
    print(f"Updated {updated} files")

    print(f"\n=== Step 3: {'[DRY RUN] ' if dry_run else ''}Rename directories ===")
    rename_dirs(dry_run=dry_run)

    if dry_run:
        print("\nDRY RUN complete. To execute: python scripts/rename_directories.py --execute")


if __name__ == "__main__":
    main()
