#!/usr/bin/env bash
# Build CurlClient for macOS
# Run this script on a Mac: bash build_mac.sh

set -e

echo "Installing dependencies..."
pip3 install requests pyinstaller --quiet

echo "Building CurlClient.app..."
pyinstaller \
  --onefile \
  --windowed \
  --name "CurlClient" \
  --clean \
  curl_client.py

echo ""
echo "Done! App is at: dist/CurlClient"
echo "You can also run it directly: python3 curl_client.py"
