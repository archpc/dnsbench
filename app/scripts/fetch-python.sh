#!/bin/bash
# Fetch the bundled Python runtime (python-build-standalone, redistributable).
# Idempotent; run once before building the app.
set -euo pipefail
VERSION="3.13.16"
TAG="20261013"
DIR="$(cd "$(dirname "$0")/.." && pwd)/Resources/python"
if [ -x "$DIR/bin/python3.13" ]; then
    echo "python runtime already present at $DIR"
    exit 0
fi
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/cpython-${VERSION}%2B${TAG}-aarch64-apple-darwin-install_only.tar.gz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -sL -o "$TMP/py.tar.gz" "$URL"
mkdir -p "$DIR"
tar xzf "$TMP/py.tar.gz" -C "$TMP"
mv "$TMP/python" "$DIR"
echo "installed to $DIR"
