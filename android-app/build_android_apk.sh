#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IS_TEST=0
if [ "${1:-}" = "--test" ] || [ "${BUILD_FLAVOR:-}" = "test" ]; then
  IS_TEST=1
fi

if [ "$IS_TEST" = "1" ]; then
  APP_STEM="TraceTodo-Test"
  PKG_ID="com.tracememory.tracetodo.test"
  APP_LABEL="TraceTodo 测试版"
  DEFAULT_URL="http://43.228.79.133:18091"
else
  APP_STEM="TraceTodo"
  PKG_ID="com.tracememory.tracetodo"
  APP_LABEL="TraceTodo"
  DEFAULT_URL="https://todo.531party.cn"
fi

# Locate JDK 17+ and Android SDK 34 (system or /tmp/tracetodo_android_sdk)
if [ -z "${JAVA_HOME:-}" ]; then
  for cand in "/tmp/tracetodo_android_sdk/jdk17" "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"; do
    if [ -x "$cand/bin/javac" ]; then
      export JAVA_HOME="$cand"
      break
    fi
  done
fi
if [ -n "${JAVA_HOME:-}" ]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [ -z "$SDK_ROOT" ]; then
  for cand in "$HOME/Library/Android/sdk" "/tmp/tracetodo_android_sdk"; do
    if [ -d "$cand/platforms/android-34" ]; then
      SDK_ROOT="$cand"
      break
    fi
  done
fi

if [ -z "$SDK_ROOT" ] || [ ! -f "$SDK_ROOT/platforms/android-34/android.jar" ]; then
  echo "❌ Android SDK platform 34 not found. Set ANDROID_SDK_ROOT or run SDK bootstrap." >&2
  exit 1
fi

BUILD_TOOLS_DIR="$(ls -d "$SDK_ROOT/build-tools/"* | sort -V | tail -n1)"
AAPT2="$BUILD_TOOLS_DIR/aapt2"
D8="$BUILD_TOOLS_DIR/d8"
ZIPALIGN="$BUILD_TOOLS_DIR/zipalign"
APKSIGNER="$BUILD_TOOLS_DIR/apksigner"
ANDROID_JAR="$SDK_ROOT/platforms/android-34/android.jar"

WORK_DIR="/tmp/tracetodo_apk_build_${APP_STEM}"
DIST_DIR="$SCRIPT_DIR/dist"
OUT_APK="$DIST_DIR/${APP_STEM}-Android.apk"
rm -rf "$WORK_DIR" "$OUT_APK"
mkdir -p "$WORK_DIR/compiled_res" "$WORK_DIR/gen" "$WORK_DIR/classes" "$WORK_DIR/dex" "$WORK_DIR/res_stage" "$DIST_DIR"

cp -R "$SCRIPT_DIR/app/src/main/res/"* "$WORK_DIR/res_stage/"
cat > "$WORK_DIR/res_stage/values/strings.xml" <<STRXML
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">${APP_LABEL}</string>
    <string name="default_server_url">${DEFAULT_URL}</string>
    <string name="fallback_server_url">http://43.228.79.133:18090</string>
    <string name="test_server_url">http://43.228.79.133:18091</string>
    <color name="ic_launcher_background">#0F172A</color>
</resources>
STRXML

echo "🤖 [1/5] Compiling Android resources with aapt2 ($APP_STEM)..."
"$AAPT2" compile --dir "$WORK_DIR/res_stage" -o "$WORK_DIR/compiled_res/resources.zip"

echo "🔗 [2/5] Linking resources & AndroidManifest (targetSdk 34, version 1.3.0)..."
"$AAPT2" link \
  -I "$ANDROID_JAR" \
  --manifest "$SCRIPT_DIR/app/src/main/AndroidManifest.xml" \
  --rename-manifest-package "$PKG_ID" \
  --min-sdk-version 26 \
  --target-sdk-version 34 \
  --version-code 3 \
  --version-name "1.3.0" \
  --java "$WORK_DIR/gen" \
  -o "$WORK_DIR/unaligned.apk" \
  "$WORK_DIR/compiled_res/resources.zip"

echo "☕ [3/5] Compiling Java sources against Android API 34..."
javac \
  --release 11 \
  -encoding UTF-8 \
  -classpath "$ANDROID_JAR" \
  -d "$WORK_DIR/classes" \
  "$WORK_DIR/gen/com/tracememory/tracetodo/R.java" \
  "$SCRIPT_DIR/app/src/main/java/com/tracememory/tracetodo/MainActivity.java"

echo "⚡ [4/5] Converting bytecode to optimized Release DEX with d8..."
CLASS_FILES=()
while IFS= read -r -d '' cf; do
  CLASS_FILES+=("$cf")
done < <(find "$WORK_DIR/classes" -name "*.class" -print0)

"$D8" \
  --release \
  --min-api 26 \
  --lib "$ANDROID_JAR" \
  --output "$WORK_DIR/dex" \
  "${CLASS_FILES[@]}"

(cd "$WORK_DIR/dex" && zip -qj "$WORK_DIR/unaligned.apk" classes.dex)

echo "🔐 [5/5] Aligning & signing Release APK (V1 + V2 + V3 signatures)..."
"$ZIPALIGN" -f -p 4 "$WORK_DIR/unaligned.apk" "$WORK_DIR/aligned.apk"

KEYSTORE_PATH="/tmp/tracetodo_release_signing.jks"
if [ ! -f "$KEYSTORE_PATH" ]; then
  keytool -genkeypair \
    -v \
    -keystore "$KEYSTORE_PATH" \
    -alias tracetodo_release \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -storepass tracetodo2026 \
    -keypass tracetodo2026 \
    -dname "CN=TraceTodo, OU=Mobile, O=TraceMemory, L=Hangzhou, S=Zhejiang, C=CN" >/dev/null 2>&1
fi

"$APKSIGNER" sign \
  --ks "$KEYSTORE_PATH" \
  --ks-key-alias tracetodo_release \
  --ks-pass pass:tracetodo2026 \
  --key-pass pass:tracetodo2026 \
  --v1-signing-enabled true \
  --v2-signing-enabled true \
  --v3-signing-enabled true \
  --out "$OUT_APK" \
  "$WORK_DIR/aligned.apk"

"$APKSIGNER" verify --verbose "$OUT_APK" | head -n 8
rm -rf "$WORK_DIR"
echo "✅ Built signed Android Release APK: $OUT_APK"
