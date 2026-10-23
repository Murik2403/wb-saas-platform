#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]] || ! command -v xcodebuild >/dev/null; then
  echo 'Requires macOS and Xcode; use the iOS build workflow.' >&2
  exit 1
fi
mkdir -p dist-ios
xcodebuild -project ios/App/App.xcodeproj -scheme App -configuration Debug \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath build-ios/simulator CODE_SIGNING_ALLOWED=NO build
ditto -c -k --keepParent build-ios/simulator/Build/Products/Debug-iphonesimulator/App.app dist-ios/Marketshelper-simulator.zip
xcodebuild -project ios/App/App.xcodeproj -scheme App -configuration Release \
  -destination 'generic/platform=iOS' -archivePath build-ios/Marketshelper.xcarchive \
  CODE_SIGNING_ALLOWED=NO archive
mkdir -p build-ios/package/Payload
ditto build-ios/Marketshelper.xcarchive/Products/Applications/App.app build-ios/package/Payload/App.app
(cd build-ios/package && /usr/bin/zip -q -r ../../dist-ios/Marketshelper-unsigned.ipa Payload)
printf '%s\n' \
  'Simulator ZIP: for iOS Simulator only.' \
  'Unsigned IPA: requires Apple signing and provisioning before installation.' \
  'Push notifications are not implemented end to end.' > dist-ios/README.txt
(cd dist-ios && shasum -a 256 Marketshelper-simulator.zip Marketshelper-unsigned.ipa > SHA256SUMS.txt)
