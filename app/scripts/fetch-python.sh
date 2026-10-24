#!/bin/bash
# Fetch the bundled Python runtime (python-build-standalone, redistributable).
# Idempotent; run once before building the app.
set -euo pipefail
VERSION="3.13.16"
TAG="20261013"
DIR="$(cd "$(dirname "$0")/.." && pwd)/Resources/python"
if [ -x "$DIR/bin/python3.13" ]; then
    "$DIR/bin/python3.13" -I -c 'import json, ssl, sys; assert sys.version_info[:2] == (3, 13)'
    echo "python runtime already present at $DIR"
    exit 0
fi
if [ -e "$DIR" ]; then
    echo "incomplete Python runtime at $DIR; move it aside before retrying" >&2
    exit 1
fi
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/cpython-${VERSION}%2B${TAG}-aarch64-apple-darwin-install_only.tar.gz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -fSL --retry 2 -o "$TMP/py.tar.gz" "$URL"
tar xzf "$TMP/py.tar.gz" -C "$TMP"
"$TMP/python/bin/python3.13" -I -c 'import json, ssl, sys; assert sys.version_info[:2] == (3, 13)'
# Create only the parent: an existing destination would nest python/python.
mkdir -p "$(dirname "$DIR")"
mv "$TMP/python" "$DIR"
echo "installed to $DIR"
