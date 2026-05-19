#!/usr/bin/env bash
# Build CurlClient for macOS — produces dist/CurlClient-macOS.dmg
set -e

echo "Installing dependencies..."
pip3 install requests pyinstaller --quiet

echo "Building CurlClient.app..."
pyinstaller \
  --windowed \
  --name "CurlClient" \
  --clean \
  curl_client.py

echo "Packaging CurlClient-macOS.dmg..."
mkdir -p dist/dmg
cp -r "dist/CurlClient.app" dist/dmg/
hdiutil create \
  -volname "CurlClient" \
  -srcfolder dist/dmg \
  -ov -format UDZO \
  "dist/CurlClient-macOS.dmg"

echo ""
echo "Done!"
echo "  App bundle : dist/CurlClient.app"
echo "  Disk image : dist/CurlClient-macOS.dmg"
