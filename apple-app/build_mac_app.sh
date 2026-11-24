#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IS_TEST=0
if [ "${1:-}" = "--test" ] || [ "${BUILD_FLAVOR:-}" = "test" ]; then
    IS_TEST=1
fi

# Auto-detect installed Xcode.app (/Applications or ~/Desktop)
XCODE_APP=""
for cand in "/Applications/Xcode.app" "$HOME/Desktop/Xcode.app" "$HOME/Applications/Xcode.app"; do
    if [ -d "$cand/Contents/Developer" ]; then
        XCODE_APP="$cand"
        break
    fi
done

if [ "$IS_TEST" = "1" ]; then
    APP_STEM="TraceTodo-Test"
    MAC_BUNDLE_ID="com.tracememory.tracetodo.test"
    IOS_BUNDLE_ID="com.tracememory.tracetodo.test.ios"
    MAC_DISPLAY_NAME="TraceTodo 测试版"
    IOS_DISPLAY_NAME="TraceTodo 测试版"
    SWIFT_EXTRA_FLAGS=(-D TRACETODO_TEST_BUILD)
    XCODE_EXTRA_SWIFT_FLAGS="-DTRACETODO_TEST_BUILD"
else
    APP_STEM="TraceTodo"
    MAC_BUNDLE_ID="com.tracememory.tracetodo"
    IOS_BUNDLE_ID="com.tracememory.tracetodo.ios"
    MAC_DISPLAY_NAME="TraceTodo 四象限待办"
    IOS_DISPLAY_NAME="TraceTodo"
    SWIFT_EXTRA_FLAGS=()
    XCODE_EXTRA_SWIFT_FLAGS=""
fi

APP_DIR="$SCRIPT_DIR/dist/${APP_STEM}.app"
MAC_ZIP="$SCRIPT_DIR/dist/${APP_STEM}-macOS.zip"
MAC_DMG="$SCRIPT_DIR/dist/${APP_STEM}-macOS.dmg"
IOS_SIM_APP_DIR="$SCRIPT_DIR/dist/iOS-Simulator/${APP_STEM}.app"
IOS_IPA="$SCRIPT_DIR/dist/${APP_STEM}-iOS.ipa"

rm -rf "$APP_DIR" "$IOS_SIM_APP_DIR" "$MAC_ZIP" "$MAC_DMG" "$IOS_IPA"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
mkdir -p "$IOS_SIM_APP_DIR"

if [ -n "$XCODE_APP" ]; then
    export DEVELOPER_DIR="$XCODE_APP/Contents/Developer"
    SWIFTC="$DEVELOPER_DIR/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
    XCODEBUILD="$DEVELOPER_DIR/usr/bin/xcodebuild"
    MAC_SDK="$DEVELOPER_DIR/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    IOS_SIM_SDK="$DEVELOPER_DIR/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk"
    MOD_CACHE="/tmp/tracetodo_swift_modcache_${APP_STEM}"
    DERIVED_DIR="/tmp/tracetodo_xcode_derived_${APP_STEM}"
    mkdir -p "$MOD_CACHE"

    echo "🍎 Detected Xcode at: $XCODE_APP (Flavor: $APP_STEM)"
    echo "🔨 [1/3] Compiling pure SwiftUI macOS native binary (arm64)..."
    "$SWIFTC" \
        -O \
        -swift-version 5 \
        -parse-as-library \
        -module-cache-path "$MOD_CACHE" \
        ${SWIFT_EXTRA_FLAGS[@]+"${SWIFT_EXTRA_FLAGS[@]}"} \
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
        ${SWIFT_EXTRA_FLAGS[@]+"${SWIFT_EXTRA_FLAGS[@]}"} \
        -target arm64-apple-ios17.0-simulator \
        -sdk "$IOS_SIM_SDK" \
        Sources/TraceTodo/*.swift \
        -o "$IOS_SIM_APP_DIR/TraceTodo"

    echo "📦 [3/3] Building real iPhoneOS Release app + WidgetKit extension (.ipa)..."
    rm -rf "$DERIVED_DIR"
    "$XCODEBUILD" \
        -project "$SCRIPT_DIR/TraceTodo.xcodeproj" \
        -scheme TraceTodo \
        -configuration Release \
        -sdk iphoneos \
        -derivedDataPath "$DERIVED_DIR" \
        CODE_SIGNING_ALLOWED=NO \
        CODE_SIGNING_REQUIRED=NO \
        CODE_SIGN_IDENTITY="" \
        OTHER_SWIFT_FLAGS="\$(inherited) ${XCODE_EXTRA_SWIFT_FLAGS}" \
        build >/tmp/tracetodo_ios_build_${APP_STEM}.log

    BUILT_IOS_APP="$DERIVED_DIR/Build/Products/Release-iphoneos/TraceTodo.app"
    if [ -d "$BUILT_IOS_APP" ]; then
        IPA_STAGE="/tmp/tracetodo_ipa_stage_${APP_STEM}"
        rm -rf "$IPA_STAGE"
        mkdir -p "$IPA_STAGE/Payload"
        cp -R "$BUILT_IOS_APP" "$IPA_STAGE/Payload/TraceTodo.app"
        /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $IOS_BUNDLE_ID" "$IPA_STAGE/Payload/TraceTodo.app/Info.plist"
        /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $IOS_DISPLAY_NAME" "$IPA_STAGE/Payload/TraceTodo.app/Info.plist"
        if [ -f "$IPA_STAGE/Payload/TraceTodo.app/PlugIns/TraceTodoWidgets.appex/Info.plist" ]; then
            /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ${IOS_BUNDLE_ID}.widgets" "$IPA_STAGE/Payload/TraceTodo.app/PlugIns/TraceTodoWidgets.appex/Info.plist"
            /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName ${IOS_DISPLAY_NAME}小组件" "$IPA_STAGE/Payload/TraceTodo.app/PlugIns/TraceTodoWidgets.appex/Info.plist" || true
        fi
        (cd "$IPA_STAGE" && zip -qry "$IOS_IPA" Payload)
        rm -rf "$IPA_STAGE"
    fi
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

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>TraceTodo</string>
    <key>CFBundleIdentifier</key>
    <string>${MAC_BUNDLE_ID}</string>
    <key>CFBundleName</key>
    <string>${APP_STEM}</string>
    <key>CFBundleDisplayName</key>
    <string>${MAC_DISPLAY_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.4.1</string>
    <key>CFBundleVersion</key>
    <string>5</string>
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

codesign --force --deep --sign - "$APP_DIR" >/dev/null 2>&1 || true
ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$MAC_ZIP"

DMG_STAGE="/tmp/tracetodo_dmg_stage_${APP_STEM}"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP_DIR" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create -volname "$MAC_DISPLAY_NAME" -srcfolder "$DMG_STAGE" -ov -format UDZO "$MAC_DMG" >/dev/null 2>&1 || true
rm -rf "$DMG_STAGE"

if [ -f "$IOS_SIM_APP_DIR/TraceTodo" ]; then
    chmod +x "$IOS_SIM_APP_DIR/TraceTodo"
    MASTER_ICON="$SCRIPT_DIR/Config/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"
    if [ -f "$MASTER_ICON" ]; then
        sips -z 120 120 "$MASTER_ICON" --out "$IOS_SIM_APP_DIR/AppIcon60x60@2x.png" >/dev/null
        sips -z 180 180 "$MASTER_ICON" --out "$IOS_SIM_APP_DIR/AppIcon60x60@3x.png" >/dev/null
    fi
    cat > "$IOS_SIM_APP_DIR/Info.plist" <<IOSPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>TraceTodo</string>
    <key>CFBundleIdentifier</key>
    <string>${IOS_BUNDLE_ID}</string>
    <key>CFBundleName</key>
    <string>${APP_STEM}</string>
    <key>CFBundleDisplayName</key>
    <string>${IOS_DISPLAY_NAME}</string>
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
    <string>1.4.1</string>
    <key>CFBundleVersion</key>
    <string>5</string>
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

echo "✅ Built macOS app: $APP_DIR"
echo "✅ Built macOS zip: $MAC_ZIP"
[ -f "$MAC_DMG" ] && echo "✅ Built macOS dmg: $MAC_DMG"
echo "✅ Built iOS Simulator app: $IOS_SIM_APP_DIR"
[ -f "$IOS_IPA" ] && echo "✅ Built iOS IPA: $IOS_IPA"
