#!/usr/bin/env bash
# sync-version.sh
# Version synchronization script for Unix/macOS/Linux
# Updates all version references from the root VERSION file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VERSION_FILE="$PROJECT_ROOT/VERSION"

if [ ! -f "$VERSION_FILE" ]; then
    echo "ERROR: VERSION file not found at $VERSION_FILE"
    exit 1
fi

VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')

if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "ERROR: Invalid version format: $VERSION (expected SemVer: x.y.z)"
    exit 1
fi

echo "Syncing version: $VERSION"

# 1. Update Cargo.toml
CARGO_TOML="$PROJECT_ROOT/src-tauri/Cargo.toml"
if [ -f "$CARGO_TOML" ]; then
    if grep -q '^version = ' "$CARGO_TOML"; then
        sed -i'' -e "s/^version = \"[^\"]*\"/version = \"$VERSION\"/" "$CARGO_TOML"
        echo "  [OK] Cargo.toml updated"
    else
        echo "  [OK] Cargo.toml already at $VERSION"
    fi
else
    echo "  [WARN] Cargo.toml not found at $CARGO_TOML"
fi

# 2. Update package.json
PACKAGE_JSON="$PROJECT_ROOT/package.json"
if [ -f "$PACKAGE_JSON" ]; then
    if command -v jq &> /dev/null; then
        CURRENT_VERSION=$(jq -r '.version' "$PACKAGE_JSON")
        if [ "$CURRENT_VERSION" != "$VERSION" ]; then
            TMP_FILE=$(mktemp)
            jq --arg ver "$VERSION" '.version = $ver' "$PACKAGE_JSON" > "$TMP_FILE"
            mv "$TMP_FILE" "$PACKAGE_JSON"
            echo "  [OK] package.json updated"
        else
            echo "  [OK] package.json already at $VERSION"
        fi
    else
        echo "  [WARN] jq not installed. Please install jq or manually update package.json"
    fi
else
    echo "  [WARN] package.json not found at $PACKAGE_JSON"
fi

# 3. Verify python/app/version.py (reads from VERSION file, no update needed)
PY_VERSION="$PROJECT_ROOT/python/app/version.py"
if [ -f "$PY_VERSION" ]; then
    echo "  [OK] python/app/version.py reads from VERSION file (no update needed)"
else
    echo "  [WARN] python/app/version.py not found at $PY_VERSION"
fi

# 4. Verification
echo ""
echo "=== Version Verification ==="

CARGO_VERSION=$(grep '^version = ' "$CARGO_TOML" | head -1 | sed 's/version = "//;s/"//')
PKG_VERSION=$(grep '"version"' "$PACKAGE_JSON" | head -1 | sed 's/.*"version": "//;s/".*//')
ROOT_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')

if [ "$CARGO_VERSION" = "$VERSION" ] && [ "$PKG_VERSION" = "$VERSION" ] && [ "$ROOT_VERSION" = "$VERSION" ]; then
    echo "All versions match: $VERSION"
else
    echo "ERROR: Version mismatch detected!"
    echo "  VERSION file:     $ROOT_VERSION"
    echo "  Cargo.toml:       $CARGO_VERSION"
    echo "  package.json:     $PKG_VERSION"
    exit 1
fi
