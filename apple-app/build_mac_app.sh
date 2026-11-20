#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-detect installed Xcode.app (/Applications or ~/Desktop)
XCODE_APP=""
for cand in "/Applications/Xcode.app" "$HOME/Desktop/Xcode.app" "$HOME/Applications/Xcode.app"; do
    if [ -d "$cand/Contents/Developer" ]; then
        XCODE_APP="$cand"
        break
    fi
done

APP_DIR="$SCRIPT_DIR/dist/TraceTodo.app"
IOS_SIM_APP_DIR="$SCRIPT_DIR/dist/iOS-Simulator/TraceTodo.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
mkdir -p "$IOS_SIM_APP_DIR"

if [ -n "$XCODE_APP" ]; then
    SWIFTC="$XCODE_APP/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
    MAC_SDK="$XCODE_APP/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    IOS_SIM_SDK="$XCODE_APP/Contents/Developer/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk"
    IOS_DEV_SDK="$XCODE_APP/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk"
    MOD_CACHE="/tmp/tracetodo_swift_modcache"
    mkdir -p "$MOD_CACHE"

    echo "🍎 Detected Xcode at: $XCODE_APP"
    echo "🔨 [1/3] Compiling pure SwiftUI macOS native binary (arm64)..."
    "$SWIFTC" \
        -O \
        -swift-version 5 \
        -parse-as-library \
        -module-cache-path "$MOD_CACHE" \
        -target arm64-apple-macos14.0 \
        -sdk "$MAC_SDK" \
        Sources/TraceTodo/*.swift \
        -o "$APP_DIR/Contents/MacOS/TraceTodo"

    echo "📱 [2/3] Compiling pure SwiftUI iPhone Simulator app bundle (arm64)..."
    "$SWIFTC" \
        -O \
        -swift-version 5 \
        -parse-as-library \
        -module-cache-path "$MOD_CACHE" \
        -target arm64-apple-ios17.0-simulator \
        -sdk "$IOS_SIM_SDK" \
        Sources/TraceTodo/*.swift \
        -o "$IOS_SIM_APP_DIR/TraceTodo"

    echo "✅ [3/3] Type-checking real iPhoneOS device target (arm64-apple-ios17.0)..."
    "$SWIFTC" \
        -typecheck \
        -swift-version 5 \
        -parse-as-library \
        -module-cache-path "$MOD_CACHE" \
        -target arm64-apple-ios17.0 \
        -sdk "$IOS_DEV_SDK" \
        Sources/TraceTodo/*.swift
else
    echo "⚠️ Xcode.app not found; building fallback Cocoa+MenuBar native macOS app..."
    /usr/bin/clang \
        -O2 -fobjc-arc -target arm64-apple-macos14.0 \
        -framework Cocoa -framework WebKit \
        mac-native/TraceTodoMac.m \
        -o "$APP_DIR/Contents/MacOS/TraceTodo"
fi

chmod +x "$APP_DIR/Contents/MacOS/TraceTodo"

if [ -f "$SCRIPT_DIR/Config/AppIcon.icns" ]; then
    cp "$SCRIPT_DIR/Config/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
fi

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>TraceTodo</string>
    <key>CFBundleIdentifier</key>
    <string>com.tracememory.tracetodo</string>
    <key>CFBundleName</key>
    <string>TraceTodo</string>
    <key>CFBundleDisplayName</key>
    <string>TraceTodo 四象限待办</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>使用原生语音识别将你的口述极速转为待办或随记</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>使用麦克风录入语音待办与随记</string>
    <key>NSCameraUsageDescription</key>
    <string>拍照附加图片到记忆与随记中</string>
    <key>NSPhotoLibraryUsageDescription</key>
    <string>选择图片附加到记忆与随记中</string>
</dict>
</plist>
PLIST

if [ -f "$IOS_SIM_APP_DIR/TraceTodo" ]; then
    chmod +x "$IOS_SIM_APP_DIR/TraceTodo"
    MASTER_ICON="$SCRIPT_DIR/Config/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"
    if [ -f "$MASTER_ICON" ]; then
        sips -z 120 120 "$MASTER_ICON" --out "$IOS_SIM_APP_DIR/AppIcon60x60@2x.png" >/dev/null
        sips -z 180 180 "$MASTER_ICON" --out "$IOS_SIM_APP_DIR/AppIcon60x60@3x.png" >/dev/null
    fi
    cat > "$IOS_SIM_APP_DIR/Info.plist" <<'IOSPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>TraceTodo</string>
    <key>CFBundleIdentifier</key>
    <string>com.tracememory.tracetodo.ios</string>
    <key>CFBundleName</key>
    <string>TraceTodo</string>
    <key>CFBundleDisplayName</key>
    <string>TraceTodo</string>
    <key>CFBundleIcons</key>
    <dict>
        <key>CFBundlePrimaryIcon</key>
        <dict>
            <key>CFBundleIconFiles</key>
            <array>
                <string>AppIcon60x60</string>
            </array>
        </dict>
    </dict>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSRequiresIPhoneOS</key>
    <true/>
    <key>UIDeviceFamily</key>
    <array>
        <integer>1</integer>
        <integer>2</integer>
    </array>
    <key>UILaunchScreen</key>
    <dict/>
    <key>UISupportedInterfaceOrientations</key>
    <array>
        <string>UIInterfaceOrientationPortrait</string>
        <string>UIInterfaceOrientationLandscapeLeft</string>
        <string>UIInterfaceOrientationLandscapeRight</string>
    </array>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>使用原生语音识别将你的口述极速转为待办或随记</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>使用麦克风录入语音待办与随记</string>
    <key>NSCameraUsageDescription</key>
    <string>拍照附加图片到记忆与随记中</string>
    <key>NSPhotoLibraryUsageDescription</key>
    <string>选择图片附加到记忆与随记中</string>
</dict>
</plist>
IOSPLIST
fi

echo "✅ Built pure SwiftUI macOS app: $APP_DIR"
echo "✅ Built pure SwiftUI iOS Simulator app: $IOS_SIM_APP_DIR"
